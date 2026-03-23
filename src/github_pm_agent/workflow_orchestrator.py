from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


class _PermissionBoundActions:
    """Wraps real actions and blocks any action_type listed in forbidden permissions."""

    def __init__(self, real_actions: Any, allowed: List[str], forbidden: List[str]) -> None:
        self._real = real_actions
        self._allowed = set(allowed)
        self._forbidden = set(forbidden)

    def _is_permitted(self, action_type: str) -> bool:
        if action_type in self._forbidden:
            return False
        if self._allowed and action_type not in self._allowed:
            return False
        return True

    def _block(self, action_type: str) -> Dict[str, Any]:
        return {"action_type": action_type, "skipped": True, "reason": "forbidden_by_role_permissions"}

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        if not self._is_permitted("comment"):
            return self._block("comment")
        return self._real.comment(target_kind, target_number, message)

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        if not self._is_permitted("comment"):
            return self._block("comment")
        return self._real.comment_on_discussion(discussion_id, number, message)

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        if not self._is_permitted("label"):
            return self._block("label")
        return self._real.add_labels(number, labels)

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        if not self._is_permitted("label"):
            return self._block("label")
        return self._real.remove_labels(number, labels)

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self._is_permitted("issue"):
            return self._block("issue")
        return self._real.create_issue(title=title, body=body, labels=labels)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _NoOpActions:
    def _skip(self, action_type: str, **extra: Any) -> Dict[str, Any]:
        return {"action_type": action_type, "skipped": True, "dry_run": True, **extra}

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        return self._skip(
            "comment",
            target_kind=target_kind,
            target_number=target_number,
            message=message,
        )

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        return self._skip(
            "comment",
            target_kind="discussion",
            target_number=number,
            discussion_id=discussion_id,
            message=message,
        )

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return self._skip("add_labels", target_kind="issue", target_number=number, labels=list(labels))

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return self._skip("remove_labels", target_kind="issue", target_number=number, labels=list(labels))

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        return self._skip("create_issue", title=title, body=body, labels=list(labels or []))


class WorkflowOrchestrator:
    def __init__(
        self,
        project_root: Path,
        engine: Any,
        actions: Any,
        client: Any,
        config: Dict[str, Any],
        agent_configs: Optional[List[Dict[str, Any]]] = None,
        agent_toolkits: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.engine = engine
        self.actions = actions
        self.client = client
        self.config = config
        self.agent_configs = agent_configs or []
        self.agent_toolkits = agent_toolkits or {}
        self.workflows_dir = self.project_root / "workflows"

    def process(self, event: Any) -> Dict[str, Any]:
        if event.event_type == "discussion_comment":
            return self._record_discussion_comment(event)
        if event.event_type == "issue_comment":
            return self._record_issue_comment(event)
        workflow = self._load_workflow(event.event_type)
        if "steps" in workflow:
            return self._process_phase_workflow(event, workflow)
        participants = self._build_participants(event.event_type, workflow)
        context: Dict[str, Any] = {"cache": {}}
        participant_results = []
        vetoed = False
        escalation_refs: List[Dict[str, Any]] = []
        veto_reason = ""
        for participant in participants:
            result = self._execute_participant(event, participant, context=context)
            participant_results.append(
                {
                    "role": participant.get("role", "pm"),
                    "action_mode": participant.get("action_mode", "respond"),
                    "priority": participant.get("priority", 0),
                    "result": result,
                }
            )
            if participant.get("action_mode", "respond") == "veto" and result.get("vetoed"):
                issue_number = self._escalate(event, "veto", result.get("veto_reason", ""))
                escalation_refs.append(
                    {
                        "issue_number": issue_number,
                        "key": f"{event.repo}#{event.target_number or 0}:{event.event_type}:veto",
                        "reason_class": "veto",
                    }
                )
                vetoed = True
                veto_reason = result.get("veto_reason", "")
                break

        failed_signals: List[Dict[str, str]] = []
        signals = workflow.get("signals", [])
        if signals:
            failed_signals = self._check_signals(event, signals)
            for failure in failed_signals:
                detail = self._build_escalation_detail(event, failure)
                issue_number = self._escalate(event, failure["type"], detail)
                escalation_refs.append(
                    {
                        "issue_number": issue_number,
                        "key": f"{event.repo}#{event.target_number or 0}:{event.event_type}:{failure['type']}",
                        "reason_class": failure["type"],
                    }
                )

        combined: Dict[str, Any] = {}
        combined["workflow"] = {
            "event_type": workflow.get("event_type", "default"),
            "participants": participants,
            "signals": signals,
            "vetoed": vetoed,
        }
        combined["participants"] = participant_results
        combined["signal_failures"] = failed_signals
        combined["escalation_refs"] = escalation_refs
        combined["escalated"] = vetoed or bool(failed_signals)
        combined["vetoed"] = vetoed
        if veto_reason:
            combined["veto_reason"] = veto_reason
        return combined

    def _post_output_comment(self, event: Any, toolkit: Any, message: str, node_id: str = "") -> None:
        """Post a comment to the event's target (discussion, issue, or PR)."""
        if event.target_kind == "discussion" and node_id:
            toolkit.comment_on_discussion(node_id, event.target_number, message)
        elif event.target_kind in ("issue", "pull_request"):
            toolkit.comment(event.target_kind, event.target_number, message)

    def _process_phase_workflow(self, event: Any, workflow: Dict[str, Any]) -> Dict[str, Any]:
        from github_pm_agent.workflow_instance import WorkflowInstance

        # Check trigger_action filter (e.g., only run on "opened" actions)
        trigger_action = workflow.get("trigger_action")
        if trigger_action:
            event_action = (event.metadata or {}).get("action", "")
            if event_action != trigger_action:
                return {
                    "skipped": True,
                    "reason": f"action={event_action!r} does not match trigger_action={trigger_action!r}",
                    "escalation_refs": [],
                }

        runtime_dir = self.engine.runtime_dir
        target_number = event.target_number
        if not target_number:
            return {"error": "event missing target_number", "escalation_refs": []}

        instance = WorkflowInstance.load(runtime_dir, event.repo, target_number)

        if not instance.get_original_event():
            instance.set_original_event(event.to_dict())

        meta = event.metadata or {}

        # Skip if workflow was terminated (capability exceeded)
        if instance.is_terminated():
            return {
                "phase": instance.get_phase(),
                "skipped": True,
                "reason": "workflow_terminated",
                "terminated_reason": instance.get_terminated_reason(),
                "escalation_refs": [],
            }

        # Skip if workflow is fully complete
        if instance.is_completed():
            return {
                "phase": instance.get_phase(),
                "skipped": True,
                "reason": "workflow_completed",
                "escalation_refs": [],
            }

        # Skip if already waiting on a gate (not a resume event)
        gate_issue_number = instance.get_gate_issue_number()
        gate_discussion_node_id = instance.get_discussion_gate_node_id()
        if (gate_issue_number or gate_discussion_node_id) and not meta.get("advance_to_phase"):
            result = {
                "phase": instance.get_phase(),
                "skipped": True,
                "reason": "gate_already_open",
                "gate_issue_number": gate_issue_number,
                "escalation_refs": [],
            }
            if gate_discussion_node_id:
                result["gate_discussion_node_id"] = gate_discussion_node_id
            return result

        # Skip if awaiting clarification answer (not a resume event)
        if instance.is_awaiting_clarification() and not meta.get("advance_to_phase"):
            return {
                "phase": instance.get_phase(),
                "skipped": True,
                "reason": "awaiting_clarification",
                "escalation_refs": [],
            }

        steps = workflow.get("steps", [])
        if not steps:
            return {"error": "workflow has no steps", "escalation_refs": []}

        advance_to = meta.get("advance_to_phase")
        if advance_to:
            instance.set_phase(advance_to)
            instance.clear_gate()

        current_phase = instance.get_phase()
        if current_phase is None:
            current_phase = steps[0]["phase"]
            instance.set_phase(current_phase)

        all_ai_outputs: List[Dict[str, Any]] = []
        gate_result: Dict[str, Any] = {}
        created_issues: List[Dict[str, Any]] = []
        issue_creation_error: str = ""

        while True:
            step = next((s for s in steps if s.get("phase") == current_phase), None)
            if step is None:
                return {"error": f"no workflow step for phase={current_phase}", "escalation_refs": []}

            # Refresh artifacts each iteration so later steps see earlier artifacts
            artifacts = meta.get("artifacts") or instance.get_artifacts()
            variables: Dict[str, Any] = {
                "discussion_title": event.title,
                "discussion_body": event.body,
                # Aliases for non-discussion event types
                "issue_title": event.title,
                "issue_body": event.body,
                "pr_title": event.title,
                "pr_body": event.body,
                "current_phase": current_phase,
            }
            for phase_name, artifact_text in artifacts.items():
                if not phase_name.startswith("_"):
                    variables[f"artifact_{phase_name}"] = artifact_text
            human_comment = meta.get("gate_human_comment", "")
            variables["human_comment"] = f"Human feedback:\n{human_comment}\n" if human_comment else ""
            pending = instance.get_pending_comments()
            variables["pending_comments"] = "\n\n---\n\n".join(pending) if pending else ""
            supplements = instance.get_user_supplements()
            if supplements:
                lines = [f"- [{s['phase']}] {s['content']}" for s in supplements]
                variables["user_supplements"] = "**Owner supplements (accumulated across gates):**\n" + "\n".join(lines) + "\n"
            else:
                variables["user_supplements"] = ""

            # Build aggregated tech proposals variable for review steps
            if current_phase == "tech_review":
                all_artifacts = instance.get_artifacts()
                proposals = {k: v for k, v in all_artifacts.items() if k.startswith("tech_proposal_")}
                if proposals:
                    parts = [f"### Proposal from {k.replace('tech_proposal_', '')}\n\n{v}" for k, v in proposals.items()]
                    variables["all_tech_proposals"] = "\n\n---\n\n".join(parts)
                else:
                    variables["all_tech_proposals"] = variables.get("artifact_tech_proposal", "")

            executors = self._resolve_step_executors(step)
            last_content = ""
            node_id = meta.get("node_id") or (
                instance.get_original_event() or {}
            ).get("metadata", {}).get("node_id", "")

            # Idempotency guard: if this phase already produced an artifact (a previous
            # event triggered the same workflow), skip re-running executors to prevent
            # duplicate comments.  Happens when discussion updated_at changes after a
            # comment is posted, generating a new event_id for the same discussion.
            # Exception: advance_to_phase events are intentional re-runs (resuming after
            # clarification or gate confirmation), so always execute them fresh so that
            # worker outputs incorporate the owner's answers and synthesis is regenerated.
            existing_artifact = instance.get_artifacts().get(current_phase)
            if existing_artifact is not None and not meta.get("rerun_phase") and not meta.get("advance_to_phase"):
                last_content = existing_artifact
                executors = []

            for executor in executors:
                exec_vars = {**variables, **executor["extra_vars"]}
                result = self.engine.run_raw_text_handler(
                    event,
                    prompt_path=step["prompt_path"],
                    role=executor["role"],
                    variables=exec_vars,
                )
                content = result.get("raw_text", "")
                last_content = content
                all_ai_outputs.append({"phase": current_phase, "role": executor["label"], "content": content})

                # Post each executor's output to the target (discussion, issue, or PR)
                if step.get("output_per_role") and content and (node_id or event.target_kind in ("issue", "pull_request")):
                    agent_id = executor["agent_id"]
                    if agent_id:
                        role_toolkit = self.agent_toolkits.get(agent_id, self.actions)
                    else:
                        matched_id = next(
                            (
                                a.get("id")
                                for a in self.config.get("agents", [])
                                if isinstance(a, dict) and a.get("role", a.get("id", "pm")) == executor["role"]
                            ),
                            None,
                        )
                        role_toolkit = self.agent_toolkits.get(matched_id, self.actions) if matched_id else self.actions
                    role_header = f"**[{executor['label']}]** — `{current_phase}`\n\n"
                    self._post_output_comment(event, role_toolkit, role_header + content, node_id)

            instance.set_artifact(current_phase, last_content)
            if step.get("output_per_role"):
                phase_outputs = [o for o in all_ai_outputs if o["phase"] == current_phase]
                for ai_out in phase_outputs:
                    instance.set_artifact(f"{current_phase}_{ai_out['role']}", ai_out["content"])
                # Save a single combined artifact so PM synthesis prompts can reference one variable
                if len(phase_outputs) > 1:
                    combined_parts = [
                        f"### {o['role']}\n\n{o['content']}" for o in phase_outputs
                    ]
                    instance.set_artifact(f"{current_phase}_combined", "\n\n---\n\n".join(combined_parts))

            # After slot-based phases: check for blocking_unknowns and post clarification
            if step.get("slots"):
                questions = self._collect_blocking_unknowns(all_ai_outputs, current_phase)
                if questions:
                    self._post_clarification(questions, event, instance, node_id, current_phase)
                    break  # suspend — wait for owner reply before continuing

            step_succeeded = False

            # Actions must run before gate creation so gates can reflect the action result.
            if step.get("action") == "create_issues":
                created_issues, issue_creation_error = self._create_issues_from_artifact(last_content, event)
                issue_refs = []
                for item in created_issues:
                    number = (item.get("result") or {}).get("number") or item.get("number")
                    title = item.get("title", "")
                    issue_refs.append({"number": number, "title": title})
                if issue_refs:
                    instance.set_created_issue_refs(issue_refs)
                if issue_creation_error:
                    break
                step_succeeded = True
            elif step.get("action") == "evaluate_design":
                eval_result = self._evaluate_design(last_content, event, instance, steps, current_phase)
                if eval_result.get("terminated"):
                    return {
                        "phase": current_phase,
                        "ai_outputs": all_ai_outputs,
                        "gate": {},
                        "artifacts": instance.get_artifacts(),
                        "created_issues": [],
                        "issue_creation_error": "",
                        "terminated": True,
                        "terminated_reason": eval_result.get("reason", ""),
                        "escalation_refs": [],
                    }
                if eval_result.get("escalated"):
                    gate_result = eval_result.get("gate", {})
                    # Re-fetch pending rather than using the snapshot captured at loop start
                    # to avoid a TOCTOU race where a comment arriving after the snapshot is lost.
                    if instance.get_pending_comments():
                        instance.clear_pending_comments()
                    break
                if eval_result.get("error"):
                    issue_creation_error = eval_result["error"]
                    # Don't advance on error — break without set_completed to allow retry
                    break
                step_succeeded = True
            else:
                step_succeeded = True

            if step.get("gate"):
                from github_pm_agent.utils import utc_now_iso
                idx = next((i for i, s in enumerate(steps) if s.get("phase") == current_phase), -1)
                next_step = steps[idx + 1] if 0 <= idx < len(steps) - 1 else None
                next_phase = next_step["phase"] if next_step else None

                owner = self.config.get("github", {}).get("owner", "")
                node_id = meta.get("node_id") or (
                    instance.get_original_event() or {}
                ).get("metadata", {}).get("node_id", "")

                # For evaluate_design phases, post the human-readable final_design
                # instead of the raw JSON that the AI produced.
                display_content = last_content
                if step.get("action") == "evaluate_design":
                    display_content = instance.get_artifacts().get("final_design") or last_content

                gate_body = f"{'@' + owner + chr(10) + chr(10) if owner else ''}**Phase `{current_phase}` complete.**\n\n{display_content}"
                if next_phase:
                    gate_body += f"\n\n---\n_Comment in this discussion to confirm and advance to **{next_phase}**._"

                posted_at = utc_now_iso()
                self.actions.comment_on_discussion(node_id, target_number, gate_body)
                if next_phase:
                    instance.set_discussion_gate(node_id, posted_at, next_phase)
                gate_result = {"gate_discussion_node_id": node_id, "gate_posted_at": posted_at, "next_phase": next_phase}
                if pending:
                    instance.clear_pending_comments()
                break  # wait for human

            # Only clear pending_comments for non-slot phases (PM/synthesis steps).
            # Slot-based phases (workers) pass pending_comments downstream so the
            # next synthesis step can incorporate the user's clarification answers.
            # Gate phases already clear pending_comments at line 444-445 before break.
            if pending and step_succeeded and not step.get("slots"):
                instance.clear_pending_comments()

            idx = next((i for i, s in enumerate(steps) if s.get("phase") == current_phase), -1)
            next_step = steps[idx + 1] if 0 <= idx < len(steps) - 1 else None
            if not next_step:
                if not instance.is_completion_comment_posted():
                    self._post_completion_summary(event, instance)
                    instance.set_completion_comment_posted()
                instance.set_completed()
                break
            current_phase = next_step["phase"]
            instance.set_phase(current_phase)
            meta = dict(meta)
            meta.pop("artifacts", None)

        return {
            "phase": current_phase,
            "ai_outputs": all_ai_outputs,
            "gate": gate_result,
            "artifacts": instance.get_artifacts(),
            "created_issues": created_issues,
            "issue_creation_error": issue_creation_error,
            "escalation_refs": [],
        }

    def _create_issues_from_artifact(
        self, content: str, event: Any
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Parse AI output as JSON issue list and create each issue. Returns (created, error)."""
        from github_pm_agent.utils import extract_json_object
        parsed = extract_json_object(content)
        if not isinstance(parsed, list):
            return [], f"issue_breakdown output was not a JSON array: {content[:200]}"
        created = []
        for item in parsed:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            result = self.actions.create_issue(
                title=item["title"],
                body=item.get("body", ""),
                labels=item.get("labels", []),
            )
            created.append(result)
        return created, ""

    def _evaluate_design(
        self,
        content: str,
        event: Any,
        instance: Any,
        steps: List[Dict[str, Any]],
        current_phase: str,
    ) -> Dict[str, Any]:
        """Parse PM's design evaluation JSON and act on the decision."""
        from github_pm_agent.utils import extract_json_object
        parsed = extract_json_object(content)
        if not isinstance(parsed, dict):
            return {"error": f"evaluate_design output was not a JSON object: {content[:200]}"}

        docker_compatible = parsed.get("docker_compatible", True)
        decision = str(parsed.get("decision", "proceed")).lower()

        if not docker_compatible or decision == "terminate":
            reason = parsed.get("escalation_reason") or "proposed solution exceeds Docker/Mac Mini capability constraints"
            instance.set_terminated(reason)
            return {"terminated": True, "reason": reason}

        if decision == "escalate":
            owner = self.config.get("github", {}).get("owner", "")
            evaluation = parsed.get("evaluation_summary", content[:500])
            gate_title = f"[workflow-gate] {event.repo} Discussion #{event.target_number} phase={current_phase}"
            gate_body = (
                f"{'@' + owner + chr(10) + chr(10) if owner else ''}"
                f"Technical design review requires human input.\n\n"
                f"**PM Evaluation:**\n{evaluation}\n\n"
                f"Reply with guidance to re-run the design review."
            )
            gate_issue = self.actions.create_issue(
                title=gate_title, body=gate_body, labels=["workflow-gate"]
            )
            gate_number: Optional[int] = None
            if isinstance(gate_issue, dict):
                gate_number = gate_issue.get("number") or (gate_issue.get("result") or {}).get("number")
            # Self-loop: gate advances back to the same phase
            if gate_number:
                instance.set_gate(gate_number, current_phase)
            return {"escalated": True, "gate": {"gate_issue_number": gate_number, "next_phase": current_phase}}

        # proceed or merge: save final design
        final_design = parsed.get("final_design", "")
        if final_design:
            instance.set_artifact("final_design", final_design)
        return {"proceeded": True}

    def _record_discussion_comment(self, event: Any) -> Dict[str, Any]:
        from github_pm_agent.workflow_instance import WorkflowInstance

        discussion_number = event.target_number
        if not discussion_number:
            return {"skipped": True, "reason": "no_discussion_number", "escalation_refs": []}
        instance = WorkflowInstance.load(self.engine.runtime_dir, event.repo, discussion_number)
        if not instance.get_phase() or instance.is_completed() or instance.is_terminated():
            return {"skipped": True, "reason": "no_active_workflow", "escalation_refs": []}
        owner_login = str(self.config.get("github", {}).get("owner", "") or "").strip()
        bot_logins = {
            str(agent.get("login", "") or "").strip()
            for agent in self.config.get("agents", [])
            if isinstance(agent, dict) and str(agent.get("login", "") or "").strip()
        }
        actor_login = str(getattr(event, "actor", "") or "").strip()
        should_record_comment = bool(event.body)
        if actor_login in bot_logins:
            should_record_comment = False
        elif owner_login:
            should_record_comment = actor_login == owner_login
        if should_record_comment:
            instance.add_pending_comment(event.body)
        return {"recorded": True, "discussion_number": discussion_number, "escalation_refs": []}

    def _record_issue_comment(self, event: Any) -> Dict[str, Any]:
        """Handle issue_comment events.

        Gate advancement for issues is handled by PhaseGateScanner polling the
        GitHub API directly, not via this event path.  Worker/agent comments
        must never re-trigger the PM workflow (feedback loop).  Therefore this
        handler always short-circuits without calling the AI.
        """
        issue_number = event.target_number
        if not issue_number:
            return {"skipped": True, "reason": "no_issue_number", "escalation_refs": []}
        from github_pm_agent.workflow_instance import WorkflowInstance
        instance = WorkflowInstance.load(self.engine.runtime_dir, event.repo, issue_number)
        if not instance.get_phase() or instance.is_completed() or instance.is_terminated():
            return {"skipped": True, "reason": "no_active_workflow", "escalation_refs": []}
        # Record owner comments as pending (for future gate support on issues)
        owner_login = str(self.config.get("github", {}).get("owner", "") or "").strip()
        actor_login = str(getattr(event, "actor", "") or "").strip()
        if owner_login and actor_login == owner_login and event.body:
            instance.add_pending_comment(event.body)
        return {"recorded": True, "issue_number": issue_number, "escalation_refs": []}

    def _post_completion_summary(self, event: Any, instance: Any) -> None:
        """Post a completion comment to the original Discussion."""
        node_id = event.metadata.get("node_id") or (
            instance.get_original_event() or {}
        ).get("metadata", {}).get("node_id")
        if not node_id:
            return
        refs = instance.get_created_issue_refs()
        issue_lines = "\n".join(
            f"- #{r['number']}: {r['title']}" if r.get("number") else f"- {r['title']}"
            for r in refs
        )
        count = len(refs)
        body = f"Workflow complete. Created {count} issue(s):\n\n{issue_lines}" if issue_lines else "Workflow complete."
        self._post_output_comment(event, self.actions, body, node_id or "")

    def _resolve_step_executors(self, step: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return execution units for a step.

        Each unit has:
          role      - role string passed to the AI engine
          agent_id  - config agent id to look up the toolkit (None = use default)
          label     - display label for comments and artifact keys
          extra_vars- additional template variables injected into the prompt

        When a step declares ``slots: N`` the system finds all agents with
        ``role: worker`` (sorted by ``worker_index``), distributes them across
        the N slots (cycling if fewer workers than slots), and injects slot
        context variables.  Steps without ``slots`` fall back to the existing
        ``roles`` list behaviour.
        """
        if step.get("slots"):
            slot_count = int(step["slots"])
            worker_agents = sorted(
                [a for a in self.agent_configs if isinstance(a, dict) and a.get("role") == "worker"],
                key=lambda a: a.get("worker_index", 999),
            )
            total_workers = len(worker_agents)
            if not total_workers:
                # No workers configured — fall back to pm for every slot
                return [
                    {
                        "role": "pm",
                        "agent_id": None,
                        "label": f"pm_slot{s}",
                        "extra_vars": {"slot_number": s, "total_slots": slot_count, "worker_index": 1, "total_workers": 1},
                    }
                    for s in range(1, slot_count + 1)
                ]
            units = []
            for slot_num in range(1, slot_count + 1):
                agent = worker_agents[(slot_num - 1) % total_workers]
                w_idx = agent.get("worker_index", (slot_num - 1) % total_workers + 1)
                units.append(
                    {
                        "role": "worker",
                        "agent_id": agent.get("id"),
                        "label": f"worker{w_idx}_slot{slot_num}",
                        "extra_vars": {
                            "slot_number": slot_num,
                            "total_slots": slot_count,
                            "worker_index": w_idx,
                            "total_workers": total_workers,
                        },
                    }
                )
            return units

        # Legacy roles-based dispatch
        roles = step.get("roles", ["pm"])
        return [{"role": role, "agent_id": None, "label": role, "extra_vars": {}} for role in roles]

    def _collect_blocking_unknowns(self, ai_outputs: List[Dict[str, Any]], phase: str) -> List[str]:
        """Extract blocking_unknowns from worker outputs in the given phase.

        Workers output this in structured form:
            blocking_unknowns: ["question 1", "question 2"]
        or
            blocking_unknowns: []
        """
        import re as _re
        questions: List[str] = []
        for out in ai_outputs:
            if out.get("phase") != phase:
                continue
            content = out.get("content", "")
            match = _re.search(r"blocking_unknowns\s*:\s*\[([^\]]*)\]", content, _re.DOTALL)
            if not match:
                continue
            raw = match.group(1).strip()
            if not raw:
                continue
            # Extract double-quoted strings first
            items = _re.findall(r'"([^"]+)"', raw)
            if not items:
                # Fallback: split on commas, strip quotes/whitespace
                items = [i.strip().strip("'\"") for i in raw.split(",") if i.strip().strip("'\"")]
            questions.extend(q for q in items if q)
        return questions

    def _post_clarification(
        self,
        questions: List[str],
        event: Any,
        instance: Any,
        node_id: str,
        phase: str,
    ) -> None:
        """Post a structured clarification comment to the Discussion and suspend the phase."""
        from github_pm_agent.utils import utc_now_iso

        numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
        body = (
            f"## 需要一些澄清\n\n"
            f"在继续分析之前，以下问题需要你的回答：\n\n"
            f"{numbered}\n\n"
            f"---\n"
            f"_直接回复这条评论即可继续，无需特别格式。_"
        )
        posted_at = utc_now_iso()
        self._post_output_comment(event, self.actions, body, node_id)
        instance.set_clarification(phase=phase, posted_at=posted_at, node_id=node_id)

    def _load_workflow(self, event_type: str) -> Dict[str, Any]:
        candidates = [self.workflows_dir / f"{event_type}.yaml", self.workflows_dir / "default.yaml"]
        for path in candidates:
            if not path.exists():
                continue
            payload = yaml.safe_load(path.read_text()) or {}
            payload.setdefault("event_type", "default" if path.name == "default.yaml" else event_type)
            payload.setdefault("participants", [])
            payload.setdefault("signals", [])
            return payload
        raise FileNotFoundError(f"missing workflow config for event_type={event_type}")

    def _build_participants(self, event_type: str, workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Build participant list from config agents filtered by event_type.
        Falls back to workflow YAML participants if no config agents defined.
        Merges conditions_by_role from workflow YAML.
        """
        if not self.agent_configs:
            return sorted(
                workflow.get("participants", []),
                key=lambda p: int(p.get("priority", 0) or 0),
            )
        conditions_by_role = workflow.get("conditions_by_role", {})
        participants = []
        for agent in self.agent_configs:
            participates_in = agent.get("participates_in", {})
            action_mode = participates_in.get(event_type)
            if not action_mode:
                continue
            role = agent.get("role", agent.get("id", "pm"))
            participant: Dict[str, Any] = {
                "id": agent.get("id", role),
                "role": role,
                "action_mode": action_mode,
                "priority": agent.get("priority", 99),
            }
            condition = conditions_by_role.get(role)
            if condition:
                participant["condition"] = condition
            participants.append(participant)
        # If no config agent participates in this event_type, fall back to workflow YAML
        if not participants:
            return sorted(
                workflow.get("participants", []),
                key=lambda p: int(p.get("priority", 0) or 0),
            )
        return sorted(participants, key=lambda p: int(p.get("priority", 0) or 0))

    def _execute_participant(
        self,
        event: Any,
        participant: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._condition_matches(event, participant.get("condition"), context=context):
            return {"skipped": True, "reason": "condition_not_met"}

        role = participant.get("role", "pm")
        action_mode = participant.get("action_mode", "respond")
        agent_id = participant.get("id", role)
        agent_toolkit = self.agent_toolkits.get(agent_id)
        if action_mode == "veto":
            return self.engine.run_veto_handler(event, role=role)

        original_actions = self.engine.actions
        original_run_ai_handler = self.engine.run_ai_handler

        def run_ai_handler_with_role(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            kwargs.setdefault("role", role)
            return original_run_ai_handler(*args, **kwargs)

        self.engine.run_ai_handler = run_ai_handler_with_role
        if action_mode == "observe":
            self.engine.actions = _NoOpActions()
        else:
            base_actions = agent_toolkit if agent_toolkit else original_actions
            role_config = self.engine.role_registry.load(role) if self.engine.role_registry else {}
            permissions = role_config.get("permissions", {})
            allowed = permissions.get("allowed", [])
            forbidden = permissions.get("forbidden", [])
            if allowed or forbidden:
                self.engine.actions = _PermissionBoundActions(base_actions, allowed, forbidden)
            elif agent_toolkit:
                self.engine.actions = agent_toolkit

        try:
            result = self.engine.process(event)
        finally:
            self.engine.actions = original_actions
            self.engine.run_ai_handler = original_run_ai_handler

        if action_mode == "observe" and isinstance(result, dict):
            action = result.get("action")
            if isinstance(action, dict):
                action["executed"] = False

        self._enforce_permissions(result, role)
        return result

    def _condition_matches(
        self,
        event: Any,
        condition: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not condition:
            return True
        if not isinstance(condition, dict):
            return False

        supported_keys = {"files_match", "labels_contain"}
        unknown_keys = set(condition) - supported_keys
        if unknown_keys:
            return False

        if "files_match" in condition:
            if event.target_kind != "pull_request" or not event.target_number:
                return False
            patterns = self._parse_patterns(condition.get("files_match"))
            changed_files = self._get_changed_files(event, context=context)
            if not patterns or not self._files_match_patterns(changed_files, patterns):
                return False

        if "labels_contain" in condition:
            required_labels = set(self._normalize_labels(condition.get("labels_contain")))
            event_labels = set(self._normalize_labels(event.metadata.get("labels", [])))
            if not required_labels or not (event_labels & required_labels):
                return False

        return True

    def _get_changed_files(self, event: Any, context: Optional[Dict[str, Any]] = None) -> List[str]:
        if event.target_kind != "pull_request" or not event.target_number:
            return []

        cache = (context or {}).setdefault("cache", {})
        cache_key = ("pull_request_files", event.repo, event.target_number)
        if cache_key not in cache:
            response = self.client.api(f"repos/{event.repo}/pulls/{event.target_number}/files", method="GET")
            cache[cache_key] = [
                item.get("filename", "")
                for item in (response if isinstance(response, list) else [])
                if isinstance(item, dict) and item.get("filename")
            ]
        return list(cache.get(cache_key, []))

    def _files_match_patterns(self, changed_files: List[str], patterns: List[str]) -> bool:
        for filename in changed_files:
            for pattern in patterns:
                if fnmatch.fnmatch(filename, pattern):
                    return True
                if pattern.startswith("**/") and fnmatch.fnmatch(filename, pattern[3:]):
                    return True
        return False

    def _parse_patterns(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [pattern.strip() for pattern in value.split("|") if pattern.strip()]
        if isinstance(value, (list, tuple, set)):
            patterns: List[str] = []
            for item in value:
                patterns.extend(self._parse_patterns(item))
            return patterns
        return [str(value).strip()] if str(value).strip() else []

    def _normalize_labels(self, values: Any) -> List[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values]
        if not isinstance(values, (list, tuple, set)):
            return [str(values)]

        normalized: List[str] = []
        for value in values:
            if isinstance(value, dict):
                label = value.get("name")
                if label:
                    normalized.append(str(label))
            elif value is not None:
                normalized.append(str(value))
        return normalized

    def _check_signals(self, event: Any, signals: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        failures: List[Dict[str, str]] = []
        for signal in signals:
            signal_type = signal.get("type")
            requirement = signal.get("require")
            if signal_type == "ci_checks":
                sha = event.metadata.get("head_sha") or event.metadata.get("sha")
                if not sha:
                    failures.append({"type": "ci_checks", "reason": "missing commit SHA for CI check lookup"})
                    continue

                response = self.client.api(f"repos/{event.repo}/commits/{sha}/check-runs", method="GET")
                check_runs = response.get("check_runs", []) if isinstance(response, dict) else []
                if requirement == "all_pass":
                    if not check_runs:
                        failures.append({"type": "ci_checks", "reason": "no CI check runs found"})
                        continue
                    failing = [
                        run.get("name") or str(run.get("id") or "unknown")
                        for run in check_runs
                        if run.get("status") != "completed" or run.get("conclusion") != "success"
                    ]
                    if failing:
                        failures.append(
                            {
                                "type": "ci_checks",
                                "reason": f"CI checks not passing: {', '.join(failing)}",
                            }
                        )
                continue

            if signal_type == "pr_approvals":
                if not event.target_number:
                    failures.append({"type": "pr_approvals", "reason": "missing pull request number for review lookup"})
                    continue

                response = self.client.api(f"repos/{event.repo}/pulls/{event.target_number}/reviews", method="GET")
                reviews = response if isinstance(response, list) else []
                latest_reviews: Dict[str, str] = {}
                for review in reviews:
                    user = (review.get("user") or {}).get("login")
                    if user:
                        latest_reviews[user] = (review.get("state") or "").upper()

                approvals = [user for user, state in latest_reviews.items() if state == "APPROVED"]
                if requirement == "minimum_1" and len(approvals) < 1:
                    failures.append({"type": "pr_approvals", "reason": "pull request has fewer than 1 approval"})
                continue

            failures.append({"type": signal_type or "unknown", "reason": "unsupported workflow signal"})
        return failures

    def _escalate(self, event: Any, reason_class: str, detail: str) -> Optional[int]:
        target_number = event.target_number or 0
        key = f"{event.repo}#{target_number}:{event.event_type}:{reason_class}"
        title = f"[Agent ESCALATE] {key}"
        open_issues = self.client.api(
            f"repos/{event.repo}/issues?labels=agent-escalate&state=open",
            method="GET",
        )
        if isinstance(open_issues, list):
            for issue in open_issues:
                if key in issue.get("title", ""):
                    return issue.get("number")

        owner = self.config.get("github", {}).get("owner", "")
        full_body = f"@{owner}\n\n{detail}" if owner else detail
        result = self.actions.create_issue(title=title, body=full_body, labels=["agent-escalate"])
        if isinstance(result, dict):
            if isinstance(result.get("result"), dict):
                return result["result"].get("number")
            return result.get("number")
        return None

    def _enforce_permissions(self, result: Dict[str, Any], role: str) -> None:
        """Mark action as skipped if it was blocked by role permissions (already skipped by wrapper)."""
        action = result.get("action") if isinstance(result, dict) else None
        if isinstance(action, dict) and action.get("raw", {}).get("reason") == "forbidden_by_role_permissions":
            action["executed"] = False

    def _build_escalation_detail(self, event: Any, failure: Dict[str, str]) -> str:
        target_number = event.target_number or 0
        return (
            f"Workflow signal failed.\n\n"
            f"repo: {event.repo}\n"
            f"event_type: {event.event_type}\n"
            f"target: {event.target_kind} #{target_number}\n"
            f"url: {event.url}\n"
            f"reason: {failure['reason']}"
        )
