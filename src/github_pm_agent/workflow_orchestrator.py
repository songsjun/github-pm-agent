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

    def _process_phase_workflow(self, event: Any, workflow: Dict[str, Any]) -> Dict[str, Any]:
        from github_pm_agent.workflow_instance import WorkflowInstance

        runtime_dir = self.engine.runtime_dir
        discussion_number = event.target_number
        if not discussion_number:
            return {"error": "discussion event missing target_number", "escalation_refs": []}

        instance = WorkflowInstance.load(runtime_dir, event.repo, discussion_number)

        if not instance.get_original_event():
            instance.set_original_event(event.to_dict())

        meta = event.metadata or {}

        # Skip if workflow is fully complete
        if instance.is_completed():
            return {
                "phase": instance.get_phase(),
                "skipped": True,
                "reason": "workflow_completed",
                "escalation_refs": [],
            }

        # Skip if already waiting on a gate (not a resume event)
        if instance.get_gate_issue_number() and not meta.get("advance_to_phase"):
            return {
                "phase": instance.get_phase(),
                "skipped": True,
                "reason": "gate_already_open",
                "gate_issue_number": instance.get_gate_issue_number(),
                "escalation_refs": [],
            }

        steps = workflow.get("steps", [])
        if not steps:
            return {"error": "discussion workflow has no steps", "escalation_refs": []}

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
                "current_phase": current_phase,
            }
            for phase_name, artifact_text in artifacts.items():
                if not phase_name.startswith("_"):
                    variables[f"artifact_{phase_name}"] = artifact_text
            gate_comment = meta.get("gate_human_comment", "")
            variables["human_comment"] = f"Human feedback:\n{gate_comment}\n" if gate_comment else ""
            pending = instance.get_pending_comments()
            variables["pending_comments"] = "\n\n---\n\n".join(pending) if pending else ""

            roles = step.get("roles", ["pm"])
            last_content = ""
            for role in roles:
                result = self.engine.run_raw_text_handler(
                    event,
                    prompt_path=step["prompt_path"],
                    role=role,
                    variables=variables,
                )
                content = result.get("raw_text", "")
                last_content = content
                all_ai_outputs.append({"phase": current_phase, "role": role, "content": content})

            instance.set_artifact(current_phase, last_content)

            if pending:
                instance.clear_pending_comments()

            if step.get("gate"):
                idx = next((i for i, s in enumerate(steps) if s.get("phase") == current_phase), -1)
                next_step = steps[idx + 1] if 0 <= idx < len(steps) - 1 else None
                next_phase = next_step["phase"] if next_step else None

                owner = self.config.get("github", {}).get("owner", "")
                gate_title = f"[workflow-gate] {event.repo} Discussion #{discussion_number} phase={current_phase}"
                gate_body = f"{'@' + owner + chr(10) + chr(10) if owner else ''}Phase **{current_phase}** complete.\n\n{last_content}"
                if next_phase:
                    gate_body += f"\n\n---\nReply or close this issue to advance to: **{next_phase}**"

                gate_issue = self.actions.create_issue(
                    title=gate_title, body=gate_body, labels=["workflow-gate"]
                )
                gate_number: Optional[int] = None
                if isinstance(gate_issue, dict):
                    gate_number = gate_issue.get("number") or (gate_issue.get("result") or {}).get("number")
                if gate_number and next_phase:
                    instance.set_gate(gate_number, next_phase)
                gate_result = {"gate_issue_number": gate_number, "next_phase": next_phase}
                break  # wait for human

            # Non-gate step: handle action and advance
            if step.get("action") == "create_issues":
                created_issues, issue_creation_error = self._create_issues_from_artifact(last_content, event)
                # Normalize and persist issue refs for completion summary
                issue_refs = []
                for item in created_issues:
                    number = (item.get("result") or {}).get("number") or item.get("number")
                    title = item.get("title", "")
                    issue_refs.append({"number": number, "title": title})
                if issue_refs:
                    instance.set_created_issue_refs(issue_refs)

            idx = next((i for i, s in enumerate(steps) if s.get("phase") == current_phase), -1)
            next_step = steps[idx + 1] if 0 <= idx < len(steps) - 1 else None
            if not next_step:
                # Post completion summary before marking done (allows retry if posting fails)
                if not instance.is_completion_comment_posted():
                    self._post_completion_summary(event, instance)
                    instance.set_completion_comment_posted()
                instance.set_completed()
                break
            current_phase = next_step["phase"]
            instance.set_phase(current_phase)
            # After advancing, read fresh artifacts from instance (not metadata)
            meta = dict(meta)
            meta.pop("artifacts", None)  # force re-read from instance on next iteration

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

    def _record_discussion_comment(self, event: Any) -> Dict[str, Any]:
        from github_pm_agent.workflow_instance import WorkflowInstance

        discussion_number = event.target_number
        if not discussion_number:
            return {"skipped": True, "reason": "no_discussion_number", "escalation_refs": []}
        instance = WorkflowInstance.load(self.engine.runtime_dir, event.repo, discussion_number)
        if not instance.get_phase() or instance.is_completed():
            return {"skipped": True, "reason": "no_active_workflow", "escalation_refs": []}
        if event.body:
            instance.add_pending_comment(event.body)
        return {"recorded": True, "discussion_number": discussion_number, "escalation_refs": []}

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
        self.actions.comment_on_discussion(node_id, event.target_number, body)

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
