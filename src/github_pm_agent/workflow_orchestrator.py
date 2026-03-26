from __future__ import annotations

import fnmatch
import inspect
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from github_pm_agent.queue_store import enqueue_pending_payload
from github_pm_agent.utils import build_requeued_event, git_auth_env


logger = logging.getLogger(__name__)


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
    MAX_CLARIFICATION_ROUNDS = 2
    MAX_PHASE_GATE_OPENS = 3

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

    def _requeue_issue_coding_phase(
        self,
        instance: Any,
        event: Any,
        *,
        phase: str,
        reason: str,
        human_comment: str = "",
        response_type: str = "",
    ) -> None:
        original_event = instance.get_original_event() or event.to_dict()
        resumed_metadata = dict(original_event.get("metadata", {}))
        resumed_metadata["advance_to_phase"] = phase
        resumed_metadata["artifacts"] = instance.get_artifacts()
        if human_comment:
            resumed_metadata["gate_human_comment"] = human_comment
        if response_type:
            resumed_metadata["gate_response_type"] = response_type
        enqueue_pending_payload(
            self.engine.runtime_dir,
            build_requeued_event(
                original_event,
                metadata=resumed_metadata,
                reason=reason,
            ),
        )

    def _load_pull_request_state(self, repo: str, pr_number: int) -> Dict[str, Any]:
        try:
            payload = self.client.api(f"repos/{repo}/pulls/{pr_number}", method="GET")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load PR state for %s#%s: %s", repo, pr_number, exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _pull_request_has_merge_conflict(self, pr_state: Dict[str, Any]) -> bool:
        mergeable_state = str(pr_state.get("mergeable_state") or "").strip().lower()
        mergeable = pr_state.get("mergeable")
        return mergeable_state == "dirty" or mergeable is False

    def _terminate_phase_limit(
        self,
        event: Any,
        instance: Any,
        *,
        phase: str,
        node_id: str,
        limit_kind: str,
        limit: int,
    ) -> None:
        if limit_kind == "clarification":
            reason = (
                f"Phase `{phase}` exceeded the automatic clarification limit ({limit} round(s)). "
                "Workflow stopped to avoid an endless clarification loop."
            )
        else:
            reason = (
                f"Phase `{phase}` exceeded the automatic gate limit ({limit} attempt(s)). "
                "Workflow stopped to avoid repeated human-confirmation loops."
            )
        self._post_output_comment(
            event,
            self.actions,
            f"{reason}\n\n_Manual intervention is required to continue._",
            node_id,
        )
        instance.set_terminated(reason)

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

        # Reset state if this is a different workflow type than the one stored
        # (e.g., issue_coding event arriving for an issue whose issue_changed workflow completed)
        current_workflow_type = workflow.get("event_type", event.event_type)
        stored_workflow_type = instance.get_workflow_type()
        if not stored_workflow_type:
            # Migration: check original_event.event_type as fallback for states set by old code
            original_ev = instance.get_original_event()
            if original_ev:
                stored_workflow_type = original_ev.get("event_type", "")
        if stored_workflow_type and stored_workflow_type != current_workflow_type:
            instance.reset_for_workflow_type(current_workflow_type)
        elif not stored_workflow_type:
            instance.set_workflow_type(current_workflow_type)

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
                # Coding workflow variables
                "issue_number": str(event.target_number or ""),
                "repo": event.repo or "",
                "default_branch": self.config.get("github", {}).get("default_branch", "main"),
                "base_branch": self.config.get("github", {}).get("default_branch", "main"),
            }
            for phase_name, artifact_text in artifacts.items():
                if not phase_name.startswith("_"):
                    variables[f"artifact_{phase_name}"] = artifact_text
            # Coding workflow convenience aliases from artifacts
            test_result = artifacts.get("test_result") or {}
            if isinstance(test_result, str):
                import json as _json_local  # noqa: PLC0415
                try:
                    test_result = _json_local.loads(test_result)
                except (_json_local.JSONDecodeError, ValueError):
                    test_result = {}
            if isinstance(test_result, dict):
                variables["test_passed"] = "true" if test_result.get("passed") else "false"
                variables["test_results"] = test_result.get("summary", "")
            else:
                variables["test_passed"] = "false"
                variables["test_results"] = ""
            variables["pr_number"] = str(artifacts.get("pr_number") or "")
            variables["pr_url"] = str(artifacts.get("pr_url") or "")
            variables["review_round"] = str(instance.get_review_round())
            variables["test_failure_context"] = str(artifacts.get("test_failure_context") or "")
            # Fetch live PR diff for code_review and fix_iteration phases
            _raw_pr_num = artifacts.get("pr_number", "")
            _pr_num_int = int(str(_raw_pr_num)) if str(_raw_pr_num).strip().isdigit() else None
            if _pr_num_int and current_phase in ("code_review", "fix_iteration", "merge_conflict_resolution"):
                try:
                    variables["pr_diff"] = self.client.get_pr_diff(_pr_num_int)
                except Exception:
                    variables["pr_diff"] = ""
            else:
                variables["pr_diff"] = ""
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
            ai_cwd: Optional[Path] = None

            # Idempotency guard: if this phase already produced an artifact (a previous
            # event triggered the same workflow), skip re-running executors to prevent
            # duplicate comments.  Happens when discussion updated_at changes after a
            # comment is posted, generating a new event_id for the same discussion.
            # Exception: advance_to_phase events are intentional re-runs (resuming after
            # clarification or gate confirmation), so always execute them fresh so that
            # worker outputs incorporate the owner's answers and synthesis is regenerated.
            existing_artifact = instance.get_artifacts().get(current_phase)
            if existing_artifact is not None and meta.get("execute_gated_action"):
                last_content = existing_artifact
                executors = []
            elif existing_artifact is not None and not meta.get("rerun_phase") and not meta.get("advance_to_phase"):
                last_content = existing_artifact
                executors = []

            if executors:
                try:
                    ai_cwd = self._prepare_phase_ai_cwd(event, step, executors, artifacts)
                except RuntimeError as exc:
                    failure_message = (
                        f"Workflow stopped during `{current_phase}`: failed to prepare repository context.\n\n{exc}"
                    )
                    if event.target_kind in ("issue", "pull_request") and event.target_number:
                        self.actions.comment(event.target_kind, event.target_number, failure_message)
                    elif event.target_kind == "discussion" and node_id:
                        self.actions.comment_on_discussion(node_id, event.target_number, failure_message)
                    instance.set_terminated(f"AI context preparation failed in {current_phase}: {exc}")
                    return {
                        "phase": current_phase,
                        "ai_outputs": all_ai_outputs,
                        "gate": {},
                        "artifacts": instance.get_artifacts(),
                        "created_issues": created_issues,
                        "issue_creation_error": issue_creation_error,
                        "terminated": True,
                        "terminated_reason": instance.get_terminated_reason(),
                        "escalation_refs": [],
                    }
                if ai_cwd and current_phase == "merge_conflict_resolution":
                    github_token = self._get_worker_github_token(executors) or self._get_default_github_token()
                    variables.update(
                        self._collect_merge_conflict_prompt_context(
                            ai_cwd,
                            default_branch=self.config.get("github", {}).get("default_branch", "main"),
                            github_token=github_token,
                        )
                    )

            for executor in executors:
                exec_vars = {**variables, **executor["extra_vars"]}
                run_handler = self.engine.run_raw_text_handler
                handler_params = inspect.signature(run_handler).parameters
                if "cwd" in handler_params:
                    result = run_handler(
                        event,
                        prompt_path=step["prompt_path"],
                        role=executor["role"],
                        variables=exec_vars,
                        cwd=str(ai_cwd) if ai_cwd else None,
                    )
                else:
                    result = run_handler(
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
                    if instance.get_clarification_round(current_phase) >= self.MAX_CLARIFICATION_ROUNDS:
                        self._terminate_phase_limit(
                            event,
                            instance,
                            phase=current_phase,
                            node_id=node_id,
                            limit_kind="clarification",
                            limit=self.MAX_CLARIFICATION_ROUNDS,
                        )
                    else:
                        self._post_clarification(questions, event, instance, node_id, current_phase)
                        instance.increment_clarification_round(current_phase)
                    break  # suspend — wait for owner reply before continuing

            step_succeeded = False

            # Actions must run before gate creation so gates can reflect the action result.
            defer_gated_action = bool(
                step.get("gate_before_action")
                and step.get("action")
                and not meta.get("execute_gated_action")
            )

            if defer_gated_action:
                step_succeeded = True
            elif step.get("action") == "create_issues":
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
            elif step.get("action") == "coding_session":
                try:
                    from github_pm_agent.coding_session import CodingSession
                    from github_pm_agent.devenv_client import DevEnvClient

                    plan = CodingSession.parse_plan(last_content)
                    if plan is None:
                        self.actions.comment("issue", event.target_number, "Failed to parse coding plan from AI output")
                        instance.set_terminated("Coding plan parse failure")
                        break

                    devenv_cfg = self.config.get("devenv", {})
                    server_url = devenv_cfg.get("server_url", "")
                    base_image = devenv_cfg.get("base_image", "python:3.12-slim")
                    # Use worker's token for git push so the PM (different token) can
                    # approve the PR without hitting "last pusher cannot self-approve".
                    github_token = (event.metadata or {}).get("github_token") or self._get_worker_github_token(executors)
                    base_branch = self.config.get("github", {}).get("default_branch", "main")

                    previous_iteration = instance.get_artifacts().get("coding_iteration", 0)
                    if not isinstance(previous_iteration, int):
                        previous_iteration = int(previous_iteration) if str(previous_iteration).isdigit() else 0

                    session = CodingSession(
                        DevEnvClient(server_url=server_url),
                        repo=event.repo,
                        issue_number=event.target_number,
                        github_token=github_token,
                        base_image=base_image,
                        base_branch=self.config.get("github", {}).get("default_branch", "main"),
                    )
                    session.iteration = max(previous_iteration + 1, 1)

                    coding_result: Dict[str, Any] = {}
                    should_break = False
                    failure_comment = ""
                    error_message = ""
                    pr_body = ""

                    try:
                        session.setup()
                        session.apply_plan(plan)
                        test_result = session.run_tests(plan)
                        test_result_data = {
                            "passed": test_result.passed,
                            "summary": test_result.summary,
                            "stdout": test_result.stdout,
                            "stderr": test_result.stderr,
                        }
                        import json as _json
                        instance.set_artifact("test_result", _json.dumps(test_result_data))
                        instance.set_artifact("coding_iteration", str(session.iteration))

                        pr_body = (
                            f"Closes #{event.target_number}\n\n"
                            f"## Summary\n\n"
                            f"AI-generated implementation for issue #{event.target_number}.\n\n"
                            f"**Branch:** `{plan.branch_name}`\n"
                            f"**Iterations:** {session.iteration}"
                        )
                        coding_result = {
                            "pr_number": None,
                            "pr_url": "",
                            "branch_name": plan.branch_name,
                            "test_passed": test_result.passed,
                            "iteration": session.iteration,
                        }

                        if test_result.passed:
                            branch_name = session.push_branch()
                            pr = session.create_pr(plan.commit_message, pr_body, base_branch)
                            instance.set_artifact("pr_number", str(pr.get("number") or ""))
                            instance.set_artifact("pr_url", str(pr.get("url") or ""))
                            instance.set_artifact("branch_name", branch_name)
                            instance.set_artifact("test_failure_context", "")
                            coding_result.update(
                                {
                                    "pr_number": pr.get("number"),
                                    "pr_url": pr.get("url"),
                                    "branch_name": branch_name,
                                }
                            )
                        elif session.iteration < session.MAX_ITERATIONS:
                            failure_context = (
                                f"Iteration: {session.iteration}\n"
                                f"Summary: {test_result.summary}\n\n"
                                f"stdout:\n```\n{test_result.stdout[-3000:] if test_result.stdout else ''}\n```\n\n"
                                f"stderr:\n```\n{test_result.stderr[-3000:] if test_result.stderr else ''}\n```"
                            )
                            instance.set_artifact("test_failure_context", failure_context)
                            original_event = instance.get_original_event() or event.to_dict()
                            resumed_metadata = dict(original_event.get("metadata", {}))
                            resumed_metadata["advance_to_phase"] = "implement"
                            resumed_metadata["artifacts"] = instance.get_artifacts()
                            enqueue_pending_payload(
                                self.engine.runtime_dir,
                                build_requeued_event(
                                    original_event,
                                    metadata=resumed_metadata,
                                    reason="implement_retry",
                                ),
                            )
                            should_break = True
                        else:
                            failure_comment = (
                                f"Tests failed after {session.iteration} iteration(s).\n\n"
                                f"{test_result.summary}"
                            )
                            instance.set_terminated(f"Tests failed after {session.iteration} iteration(s)")
                            should_break = True
                    except Exception as exc:  # noqa: BLE001
                        error_message = str(exc)
                    finally:
                        cleanup_error = ""
                        try:
                            session.cleanup()
                        except Exception as exc:  # noqa: BLE001
                            cleanup_error = str(exc)
                        if cleanup_error:
                            if error_message:
                                error_message = f"{error_message}; cleanup failed: {cleanup_error}"
                            else:
                                error_message = f"cleanup failed: {cleanup_error}"

                    if error_message:
                        self.actions.comment("issue", event.target_number, f"Coding session failed: {error_message}")
                        instance.set_terminated(f"Coding session error: {error_message[:200]}")
                        break

                    if failure_comment:
                        self.actions.comment("issue", event.target_number, failure_comment)

                    self.actions.coding_session(
                        issue_number=event.target_number,
                        repo=event.repo,
                        branch_name=plan.branch_name,
                        pr_title=plan.commit_message,
                        pr_body=pr_body,
                        base_branch=base_branch,
                        coding_result=coding_result,
                    )
                    if should_break:
                        break
                    step_succeeded = True
                except Exception as exc:  # noqa: BLE001
                    self.actions.comment("issue", event.target_number, f"Coding session failed: {exc}")
                    instance.set_terminated(f"Coding session error: {str(exc)[:200]}")
                    break
            elif step.get("action") == "run_tests":
                artifacts = instance.get_artifacts()
                _raw_pr = artifacts.get("pr_number", "")
                pr_number: Optional[int] = int(str(_raw_pr)) if str(_raw_pr).strip().isdigit() else None
                if pr_number is None:
                    import logging

                    logging.getLogger(__name__).warning(
                        "Skipping run_tests action for %s#%s: missing pr_number artifact",
                        event.repo,
                        event.target_number,
                    )
                    step_succeeded = True
                else:
                    test_result_data = artifacts.get("test_result", {})
                    if not isinstance(test_result_data, dict):
                        test_result_data = {}
                    self.actions.run_tests(
                        pr_number=pr_number,
                        test_passed=test_result_data.get("passed", False),
                        test_summary=test_result_data.get("summary", ""),
                        stdout=test_result_data.get("stdout", ""),
                        stderr=test_result_data.get("stderr", ""),
                    )
                    step_succeeded = True
            elif step.get("action") == "check_review_result":
                # Decide whether to approve/advance or loop back for fixes.
                artifacts = instance.get_artifacts()
                combined_review = (
                    artifacts.get("code_review_combined")
                    or artifacts.get("code_review", "")
                )
                review_summary = self._summarize_review_artifact(combined_review)
                review_round = instance.get_review_round()
                MAX_REVIEW_ROUNDS = 3
                original_event = instance.get_original_event() or event.to_dict()

                _raw_pr = artifacts.get("pr_number", "")
                _pr_num = int(str(_raw_pr)) if str(_raw_pr).strip().isdigit() else None

                if review_summary["contract_violation"]:
                    self.actions.comment(
                        "issue",
                        event.target_number,
                        "Automated code review output could not be machine-verified. Manual review required before merge.",
                    )
                    instance.set_terminated("Code review output was not machine-verifiable")
                elif review_summary["blocking_count"] == 0:
                    # LGTM or warnings only → approve PR, advance to pm_decision
                    if _pr_num:
                        pr_state = self._load_pull_request_state(event.repo, _pr_num)
                        if self._pull_request_has_merge_conflict(pr_state):
                            conflict_reason = (
                                f"PR #{_pr_num} no longer merges cleanly against "
                                f"`{self.config.get('github', {}).get('default_branch', 'main')}`. "
                                "Resolve the branch conflict before opening the final merge gate."
                            )
                            self.actions.comment("issue", event.target_number, conflict_reason)
                            self._requeue_issue_coding_phase(
                                instance,
                                event,
                                phase="merge_conflict_resolution",
                                reason="review_conflict_detected",
                                human_comment=conflict_reason,
                                response_type="merge_conflict",
                            )
                            step_succeeded = True
                            break
                    if _pr_num:
                        try:
                            self.actions.submit_pr_review(
                                _pr_num, "APPROVE",
                                "Automated code review passed — no blocking issues found."
                            )
                        except Exception:
                            pass  # best-effort; branch protection may still block merge
                    resumed_metadata = dict(original_event.get("metadata", {}))
                    resumed_metadata["advance_to_phase"] = "pm_decision"
                    resumed_metadata["artifacts"] = instance.get_artifacts()
                    enqueue_pending_payload(
                        self.engine.runtime_dir,
                        build_requeued_event(
                            original_event,
                            metadata=resumed_metadata,
                            reason="review_approved",
                        ),
                    )
                elif review_round >= MAX_REVIEW_ROUNDS:
                    # Too many rounds — close PR, terminate, flag for human
                    escalation_msg = (
                        f"Code review found blocking issues after {review_round} round(s). "
                        f"Automated fix limit reached — manual intervention required.\n\n"
                        f"Latest review:\n{combined_review[:1500]}"
                    )
                    self.actions.comment("issue", event.target_number, escalation_msg)
                    if _pr_num:
                        try:
                            self.client.api(
                                f"repos/{event.repo}/pulls/{_pr_num}",
                                {"state": "closed"},
                                method="PATCH",
                            )
                        except Exception:
                            pass
                    instance.set_terminated(f"Code review exceeded {MAX_REVIEW_ROUNDS} rounds")
                else:
                    # Blocking issues remain — go to fix_iteration
                    instance.set_review_round(review_round + 1)
                    resumed_metadata = dict(original_event.get("metadata", {}))
                    resumed_metadata["advance_to_phase"] = "fix_iteration"
                    resumed_metadata["artifacts"] = instance.get_artifacts()
                    enqueue_pending_payload(
                        self.engine.runtime_dir,
                        build_requeued_event(
                            original_event,
                            metadata=resumed_metadata,
                            reason="review_blocking",
                        ),
                    )
                step_succeeded = True
                break  # always stop current loop; re-queue handles continuation
            elif step.get("action") == "fix_coding_session":
                try:
                    from github_pm_agent.coding_session import BranchSyncError, CodingSession
                    from github_pm_agent.devenv_client import DevEnvClient

                    plan = CodingSession.parse_plan(last_content)
                    if plan is None:
                        self.actions.comment(
                            "issue", event.target_number,
                            "Failed to parse fix plan from AI output — manual fix required."
                        )
                        instance.set_terminated("Fix plan parse failure")
                        break

                    devenv_cfg = self.config.get("devenv", {})
                    server_url = devenv_cfg.get("server_url", "")
                    base_image = devenv_cfg.get("base_image", "python:3.12-slim")
                    # Use worker's token so PM (different account) can approve the PR.
                    github_token = (event.metadata or {}).get("github_token") or self._get_worker_github_token(executors)

                    session = CodingSession(
                        DevEnvClient(server_url=server_url),
                        repo=event.repo,
                        issue_number=event.target_number,
                        github_token=github_token,
                        base_image=base_image,
                        base_branch=self.config.get("github", {}).get("default_branch", "main"),
                    )

                    error_message = ""
                    requeue_conflict_message = ""
                    original_event = instance.get_original_event() or event.to_dict()

                    try:
                        session.setup()
                        if current_phase == "merge_conflict_resolution":
                            session.resolve_merge_conflict(
                                plan,
                                base_branch=self.config.get("github", {}).get("default_branch", "main"),
                            )
                        else:
                            session.fix_and_push(plan)
                        test_result = session.run_tests(plan)
                        if test_result.passed and current_phase == "merge_conflict_resolution":
                            session.push_existing_branch(plan.branch_name)
                        import json as _json
                        instance.set_artifact("test_result", _json.dumps({
                            "passed": test_result.passed,
                            "summary": test_result.summary,
                            "stdout": test_result.stdout,
                            "stderr": test_result.stderr,
                        }))

                        resumed_metadata = dict(original_event.get("metadata", {}))
                        if test_result.passed:
                            instance.set_artifact("test_failure_context", "")
                            resumed_metadata["advance_to_phase"] = "code_review"
                            resumed_metadata["artifacts"] = instance.get_artifacts()
                            enqueue_pending_payload(
                                self.engine.runtime_dir,
                                build_requeued_event(
                                    original_event,
                                    metadata=resumed_metadata,
                                    reason="fix_review_retry",
                                ),
                            )
                        else:
                            # Fix did not make tests pass — escalate
                            fix_round = instance.get_review_round()
                            self.actions.comment(
                                "issue", event.target_number,
                                f"Fix attempt (round {fix_round}) failed — tests still not passing.\n\n"
                                f"{test_result.summary}"
                            )
                            instance.set_terminated(f"Fix tests failed at round {fix_round}")
                    except BranchSyncError as exc:
                        requeue_conflict_message = str(exc)
                    except Exception as exc:  # noqa: BLE001
                        error_message = str(exc)
                    finally:
                        try:
                            session.cleanup()
                        except Exception as cleanup_exc:  # noqa: BLE001
                            if error_message:
                                error_message = f"{error_message}; cleanup: {cleanup_exc}"
                            else:
                                error_message = f"cleanup: {cleanup_exc}"

                    if requeue_conflict_message:
                        if current_phase == "merge_conflict_resolution":
                            self.actions.comment(
                                "issue",
                                event.target_number,
                                f"Merge conflict resolution failed.\n\n{requeue_conflict_message}",
                            )
                            instance.set_terminated(f"Merge conflict resolution failed: {requeue_conflict_message[:160]}")
                        else:
                            self.actions.comment(
                                "issue",
                                event.target_number,
                                f"Branch sync failed after the fix attempt.\n\n{requeue_conflict_message}",
                            )
                            self._requeue_issue_coding_phase(
                                instance,
                                event,
                                phase="merge_conflict_resolution",
                                reason="fix_rebase_conflict",
                                human_comment=requeue_conflict_message,
                                response_type="merge_conflict",
                            )
                            step_succeeded = True
                        break

                    if error_message:
                        self.actions.comment(
                            "issue", event.target_number,
                            f"Fix iteration failed: {error_message}"
                        )
                        instance.set_terminated(f"Fix iteration error: {error_message[:200]}")
                        break

                    step_succeeded = True
                    break  # re-queue handles continuation
                except Exception as exc:  # noqa: BLE001
                    self.actions.comment("issue", event.target_number, f"Fix iteration failed: {exc}")
                    instance.set_terminated(str(exc)[:200])
                    break
            elif step.get("action") == "merge_or_reopen":
                from github_pm_agent.utils import extract_json_object

                parsed = extract_json_object(last_content)
                artifacts = instance.get_artifacts()
                review_summary = self._summarize_review_artifact(
                    artifacts.get("code_review_combined")
                    or artifacts.get("code_review", "")
                )
                deterministic = self._deterministic_pm_decision(artifacts, review_summary)
                decision = deterministic["decision"]
                reason = deterministic["reason"]
                reopen_comment = deterministic["reopen_comment"]
                if isinstance(parsed, dict):
                    parsed_reason = str(parsed.get("reason", "")).strip()
                    parsed_reopen_comment = str(parsed.get("reopen_comment", "")).strip()
                    if parsed_reason:
                        reason = parsed_reason
                    if parsed_reopen_comment:
                        reopen_comment = parsed_reopen_comment
                if decision == "merge":
                    reopen_comment = ""

                _raw_pr = artifacts.get("pr_number", "")
                pr_number: Optional[int] = int(str(_raw_pr)) if str(_raw_pr).strip().isdigit() else None
                issue_number = event.target_number
                if pr_number is None:
                    import logging

                    logging.getLogger(__name__).warning(
                        "Skipping merge_or_reopen action for %s#%s: missing pr_number artifact",
                        event.repo,
                        event.target_number,
                    )
                    step_succeeded = True
                else:
                    if decision == "merge":
                        pr_state = self._load_pull_request_state(event.repo, pr_number)
                        if self._pull_request_has_merge_conflict(pr_state):
                            default_branch = self.config.get("github", {}).get("default_branch", "main")
                            conflict_reason = (
                                f"PR #{pr_number} no longer merges cleanly against `{default_branch}`. "
                                f"Update the branch on the latest `{default_branch}`, resolve conflicts, rerun tests, and send it back through review."
                            )
                            self.actions.comment("issue", issue_number, conflict_reason)
                            if pending:
                                instance.clear_pending_comments()
                            self._requeue_issue_coding_phase(
                                instance,
                                event,
                                phase="merge_conflict_resolution",
                                reason="merge_conflict",
                                human_comment=conflict_reason,
                                response_type="merge_conflict",
                            )
                            step_succeeded = True
                            break
                    try:
                        self.actions.merge_or_reopen(
                            pr_number=pr_number,
                            issue_number=issue_number,
                            decision=decision,
                            reason=reason,
                            reopen_comment=(reopen_comment or reason) if decision == "reopen" else "",
                        )
                        step_succeeded = True
                    except Exception as exc:  # noqa: BLE001
                        if decision == "merge":
                            pr_state = self._load_pull_request_state(event.repo, pr_number)
                            if self._pull_request_has_merge_conflict(pr_state):
                                default_branch = self.config.get("github", {}).get("default_branch", "main")
                                conflict_reason = (
                                    f"Merge of PR #{pr_number} failed because the branch is out of date with `{default_branch}`. "
                                    f"Update the branch, resolve conflicts, rerun tests, and return to review against `{default_branch}`."
                                )
                                self.actions.comment("issue", issue_number, conflict_reason)
                                if pending:
                                    instance.clear_pending_comments()
                                self._requeue_issue_coding_phase(
                                    instance,
                                    event,
                                    phase="merge_conflict_resolution",
                                    reason="merge_conflict_retry",
                                    human_comment=conflict_reason,
                                    response_type="merge_conflict",
                                )
                                step_succeeded = True
                                break
                        failure_comment = f"Final `{decision}` action failed: {exc}"
                        self.actions.comment("issue", issue_number, failure_comment)
                        instance.set_terminated(failure_comment[:200])
                        break
            else:
                step_succeeded = True

            if step.get("gate") and not meta.get("execute_gated_action"):
                from github_pm_agent.utils import utc_now_iso
                owner = self.config.get("github", {}).get("owner", "")
                node_id = meta.get("node_id") or (
                    instance.get_original_event() or {}
                ).get("metadata", {}).get("node_id", "")
                if instance.get_gate_open_count(current_phase) >= self.MAX_PHASE_GATE_OPENS:
                    self._terminate_phase_limit(
                        event,
                        instance,
                        phase=current_phase,
                        node_id=node_id,
                        limit_kind="gate",
                        limit=self.MAX_PHASE_GATE_OPENS,
                    )
                    break
                idx = next((i for i, s in enumerate(steps) if s.get("phase") == current_phase), -1)
                next_step = steps[idx + 1] if 0 <= idx < len(steps) - 1 else None
                next_phase = next_step["phase"] if next_step else None
                gate_next_phase = next_phase
                gate_resume_mode = "advance"
                if step.get("gate_before_action") and step.get("action"):
                    gate_next_phase = current_phase
                    gate_resume_mode = "execute_action"

                # For evaluate_design phases, post the human-readable final_design
                # instead of the raw JSON that the AI produced.
                display_content = last_content
                if step.get("action") == "evaluate_design":
                    display_content = instance.get_artifacts().get("final_design") or last_content
                elif step.get("action") == "merge_or_reopen":
                    decision_preview = self._deterministic_pm_decision(
                        instance.get_artifacts(),
                        self._summarize_review_artifact(
                            instance.get_artifacts().get("code_review_combined")
                            or instance.get_artifacts().get("code_review", "")
                        ),
                    )
                    display_content = self._summarize_pm_decision_output(last_content) or decision_preview["reason"]

                gate_body = f"{'@' + owner + chr(10) + chr(10) if owner else ''}**Phase `{current_phase}` complete.**\n\n{display_content}"
                if gate_resume_mode == "execute_action":
                    gate_body += "\n\n---\n_Comment to confirm and execute this decision._"
                elif next_phase:
                    gate_body += f"\n\n---\n_Comment to confirm and advance to **{next_phase}**._"

                posted_at = utc_now_iso()
                self._post_output_comment(event, self.actions, gate_body, node_id)
                instance.increment_gate_open_count(current_phase)
                if event.target_kind == "discussion" and node_id:
                    instance.set_discussion_gate(
                        node_id,
                        posted_at,
                        gate_next_phase or current_phase,
                        resume_mode=gate_resume_mode,
                    )
                    gate_result = {
                        "gate_discussion_node_id": node_id,
                        "gate_posted_at": posted_at,
                        "next_phase": gate_next_phase,
                    }
                elif event.target_kind in ("issue", "pull_request") and target_number:
                    instance.set_gate(
                        target_number,
                        gate_next_phase or current_phase,
                        posted_at=posted_at,
                        resume_mode=gate_resume_mode,
                    )
                    gate_result = {
                        "gate_issue_number": target_number,
                        "gate_posted_at": posted_at,
                        "next_phase": gate_next_phase,
                    }
                else:
                    gate_result = {"gate_posted_at": posted_at, "next_phase": gate_next_phase}
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
            "terminated": instance.is_terminated(),
            "terminated_reason": instance.get_terminated_reason(),
            "escalation_refs": [],
        }

    def _summarize_review_artifact(self, content: str) -> Dict[str, Any]:
        summary = {
            "blocking_count": 0,
            "warning_count": 0,
            "contract_violation": False,
            "has_lgtm": False,
        }
        segments = self._split_review_outputs(content)
        if not segments:
            summary["contract_violation"] = True
            return summary

        for segment in segments:
            segment_summary = self._summarize_single_review_output(segment)
            if segment_summary["contract_violation"]:
                summary["contract_violation"] = True
                return summary
            summary["blocking_count"] += segment_summary["blocking_count"]
            summary["warning_count"] += segment_summary["warning_count"]
            summary["has_lgtm"] = summary["has_lgtm"] or segment_summary["has_lgtm"]
        return summary

    def _split_review_outputs(self, content: str) -> List[str]:
        stripped = content.strip()
        if not stripped:
            return []
        if "### " not in stripped and re.search(r"(?m)^\s*---\s*$", stripped) is None:
            return [stripped]

        segments: List[str] = []
        for chunk in re.split(r"(?m)^\s*---\s*$", stripped):
            cleaned_lines = [
                line
                for line in chunk.splitlines()
                if re.match(r"^\s*###\s+", line) is None
            ]
            cleaned = "\n".join(cleaned_lines).strip()
            if cleaned:
                segments.append(cleaned)
        return segments or [stripped]

    def _summarize_single_review_output(self, content: str) -> Dict[str, Any]:
        stripped = content.strip()
        summary = {
            "blocking_count": 0,
            "warning_count": 0,
            "contract_violation": False,
            "has_lgtm": False,
        }
        if not stripped:
            summary["contract_violation"] = True
            return summary
        if re.fullmatch(r"LGTM\s*[—-]\s*no issues found\.\s*", stripped, re.IGNORECASE):
            summary["has_lgtm"] = True
            return summary

        blocks = list(
            re.finditer(
                r"(?ms)^\*\*(Blocking|Warning)\*\*.*?(?=^\*\*(?:Blocking|Warning)\*\*|\Z)",
                stripped,
            )
        )
        if not blocks:
            summary["contract_violation"] = True
            return summary

        # Allow whitespace between blocks but reject any non-whitespace content
        # that does not belong to a recognised **Blocking**/**Warning** block.
        non_block = re.sub(
            r"(?ms)^\*\*(?:Blocking|Warning)\*\*.*?(?=^\*\*(?:Blocking|Warning)\*\*|\Z)",
            "",
            stripped,
        )
        if non_block.strip():
            summary["contract_violation"] = True
            return summary

        for block in blocks:
            block_text = block.group(0)
            severity = re.search(r"(?im)^\-\s+\*\*Severity:\*\*\s*(blocking|warning)\s*$", block_text)
            if severity is None:
                summary["contract_violation"] = True
                return summary
            if severity.group(1).lower() == "blocking":
                summary["blocking_count"] += 1
            else:
                summary["warning_count"] += 1
        return summary

    def _load_test_result_artifact(self, artifacts: Dict[str, Any]) -> Dict[str, Any]:
        test_result = artifacts.get("test_result") or {}
        if isinstance(test_result, str):
            import json as _json_local  # noqa: PLC0415

            try:
                loaded = _json_local.loads(test_result)
            except (_json_local.JSONDecodeError, ValueError):
                logger.warning("Failed to parse test_result artifact as JSON: %r", test_result[:200])
                return {}
            return loaded if isinstance(loaded, dict) else {}
        return test_result if isinstance(test_result, dict) else {}

    def _deterministic_pm_decision(
        self,
        artifacts: Dict[str, Any],
        review_summary: Dict[str, Any],
    ) -> Dict[str, str]:
        test_result = self._load_test_result_artifact(artifacts)
        test_passed = bool(test_result.get("passed"))
        if review_summary["contract_violation"]:
            return {
                "decision": "reopen",
                "reason": "Automated review output was not machine-verifiable, so merge was blocked.",
                "reopen_comment": "The automated review output could not be verified. Re-run review or inspect manually before retrying.",
            }
        if not test_passed:
            return {
                "decision": "reopen",
                "reason": "Tests did not pass, so the change cannot be merged.",
                "reopen_comment": "Tests must pass before merge. Fix the failing tests and rerun the workflow.",
            }
        if review_summary["blocking_count"] > 0:
            return {
                "decision": "reopen",
                "reason": f"{review_summary['blocking_count']} blocking review issue(s) remain unresolved.",
                "reopen_comment": "Resolve the blocking review issues and rerun the workflow.",
            }
        return {
            "decision": "merge",
            "reason": "Tests passed and no blocking review issues remain.",
            "reopen_comment": "",
        }

    def _summarize_pm_decision_output(self, content: str) -> str:
        stripped = content.strip()
        if not stripped:
            return ""
        if stripped.startswith("```json"):
            summary = re.sub(r"(?s)^```json\s*.*?```\s*", "", stripped).strip()
            if summary:
                return summary
        if stripped.startswith("{"):
            return ""
        return stripped

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
            labels = []
            for label in item.get("labels", []):
                label_text = str(label).strip()
                if label_text and label_text not in labels:
                    labels.append(label_text)
            if "ready-to-code" not in labels:
                labels.append("ready-to-code")
            result = self.actions.create_issue(
                title=item["title"],
                body=item.get("body", ""),
                labels=labels,
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
        if should_record_comment and (
            instance.is_awaiting_clarification()
            or instance.get_gate_next_phase()
            or instance.get_discussion_gate_node_id()
            or instance.get_gate_issue_number()
        ):
            return {
                "recorded": False,
                "discussion_number": discussion_number,
                "reason": "handled_by_gate_scanner",
                "escalation_refs": [],
            }
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

    def _get_worker_github_token(self, executors: List[Dict[str, Any]]) -> str:
        """Resolve the GitHub token for the first worker executor in the step.

        Worker agents push code to GitHub. Using the worker's own token (separate
        from the PM's token) ensures the PR reviewer (PM) is a different GitHub user
        than the last pusher (worker), satisfying the "require approval from someone
        other than last pusher" branch protection rule.

        Falls back to the config ``github.token`` or empty string if no worker token
        is configured.
        """
        for executor in executors:
            agent_id = executor.get("agent_id")
            if not agent_id:
                continue
            agent_cfg = next(
                (a for a in self.agent_configs if isinstance(a, dict) and a.get("id") == agent_id),
                None,
            )
            if agent_cfg is None:
                continue
            token_env = agent_cfg.get("token_env")
            if token_env:
                token = os.environ.get(token_env, "")
                if token:
                    return token
        return self.config.get("github", {}).get("token", "")

    def _get_default_github_token(self) -> str:
        for agent_cfg in sorted(
            [a for a in self.agent_configs if isinstance(a, dict)],
            key=lambda a: a.get("priority", 99),
        ):
            token_env = agent_cfg.get("token_env")
            if token_env:
                token = os.environ.get(token_env, "")
                if token:
                    return token
        return self.config.get("github", {}).get("token", "")

    def _prepare_phase_ai_cwd(
        self,
        event: Any,
        step: Dict[str, Any],
        executors: List[Dict[str, Any]],
        artifacts: Dict[str, Any],
    ) -> Optional[Path]:
        """Clone the target repo into a readable local context for coding-related AI phases."""
        if step.get("action") not in {"coding_session", "check_review_result", "fix_coding_session"}:
            return None

        safe_repo = str(event.repo or "").replace("/", "__", 1)
        target_number = str(event.target_number or "none")
        context_dir = self.engine.runtime_dir / "ai_context" / safe_repo / target_number
        if context_dir.exists():
            shutil.rmtree(context_dir, ignore_errors=True)
        context_dir.parent.mkdir(parents=True, exist_ok=True)

        github_token = self._get_worker_github_token(executors) or self._get_default_github_token()
        clone_url = f"https://github.com/{event.repo}.git"
        git_env = git_auth_env(github_token)

        try:
            clone_result = subprocess.run(
                ["git", "clone", clone_url, str(context_dir)],
                capture_output=True,
                text=True,
                check=False,
                env=git_env,
            )
            if clone_result.returncode != 0:
                raise RuntimeError(
                    f"git clone failed for {event.repo}: "
                    f"{clone_result.stderr.strip() or clone_result.stdout.strip() or 'no output'}"
                )

            branch_name = str(artifacts.get("branch_name") or "").strip()
            if branch_name:
                fetch_result = subprocess.run(
                    ["git", "fetch", "origin", self._remote_branch_refspec(branch_name)],
                    cwd=str(context_dir),
                    capture_output=True,
                    text=True,
                    check=False,
                    env=git_env,
                )
                if fetch_result.returncode != 0:
                    raise RuntimeError(
                        f"git fetch failed for branch {branch_name}: "
                        f"{fetch_result.stderr.strip() or fetch_result.stdout.strip() or 'no output'}"
                    )

                checkout_result = subprocess.run(
                    ["git", "checkout", branch_name],
                    cwd=str(context_dir),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if checkout_result.returncode != 0:
                    fallback_result = subprocess.run(
                        ["git", "checkout", "-B", branch_name, f"origin/{branch_name}"],
                        cwd=str(context_dir),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if fallback_result.returncode != 0:
                        raise RuntimeError(
                            f"git checkout failed for branch {branch_name}: "
                            f"{fallback_result.stderr.strip() or fallback_result.stdout.strip() or 'no output'}"
                        )
        except Exception:
            shutil.rmtree(context_dir, ignore_errors=True)
            raise

        return context_dir

    def _collect_merge_conflict_prompt_context(
        self,
        context_dir: Path,
        *,
        default_branch: str,
        github_token: str,
    ) -> Dict[str, str]:
        git_env = git_auth_env(github_token)
        base_ref = f"origin/{default_branch}"
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", default_branch],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
            check=False,
            env=git_env,
        )
        if fetch_result.returncode != 0:
            details = fetch_result.stderr.strip() or fetch_result.stdout.strip() or "no output"
            return {
                "merge_conflict_details": (
                    f"Failed to refresh `{base_ref}` before probing merge conflicts: {details}"
                ),
                "merge_conflict_files": "",
            }

        merge_result = subprocess.run(
            [
                "git",
                "-c",
                "user.name=github-pm-agent",
                "-c",
                "user.email=github-pm-agent@local",
                "merge",
                "--no-commit",
                "--no-ff",
                base_ref,
            ],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        status_result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        unmerged_result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        unmerged_files = [line.strip() for line in unmerged_result.stdout.splitlines() if line.strip()]
        status_text = status_result.stdout.strip()

        snippets = self._conflict_file_snippets(context_dir, unmerged_files)
        summary_lines = [
            f"Local merge replay against `{base_ref}` returned exit code {merge_result.returncode}.",
        ]
        first_line = (
            self._first_nonempty_line(merge_result.stderr)
            or self._first_nonempty_line(merge_result.stdout)
        )
        if first_line:
            summary_lines.append(first_line)
        if unmerged_files:
            summary_lines.append("Conflicted files:\n" + "\n".join(f"- `{path}`" for path in unmerged_files[:8]))
        else:
            summary_lines.append("Git did not report unmerged files after the local replay.")
        if status_text:
            summary_lines.append("Git status after replay:\n" + status_text[:1200])
        if snippets:
            summary_lines.append("Conflict excerpts:\n" + "\n\n".join(snippets))

        self._cleanup_merge_probe(context_dir)
        return {
            "merge_conflict_details": "\n\n".join(summary_lines)[:6000],
            "merge_conflict_files": "\n".join(f"- `{path}`" for path in unmerged_files[:8]),
        }

    @staticmethod
    def _remote_branch_refspec(branch_name: str) -> str:
        return f"{branch_name}:refs/remotes/origin/{branch_name}"

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    def _conflict_file_snippets(
        self,
        context_dir: Path,
        paths: List[str],
        *,
        max_files: int = 3,
        context_lines: int = 3,
    ) -> List[str]:
        snippets: List[str] = []
        for relative_path in paths[:max_files]:
            file_path = (context_dir / relative_path).resolve()
            try:
                file_path.relative_to(context_dir.resolve())
                content = file_path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
            lines = content.splitlines()
            for index, line in enumerate(lines):
                if not line.startswith("<<<<<<<"):
                    continue
                start = max(index - context_lines, 0)
                end = min(index + context_lines + 8, len(lines))
                excerpt = "\n".join(
                    f"{line_no + 1}: {lines[line_no]}"
                    for line_no in range(start, end)
                )
                snippets.append(f"`{relative_path}`\n```text\n{excerpt}\n```")
                break
        return snippets

    @staticmethod
    def _cleanup_merge_probe(context_dir: Path) -> None:
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
            check=False,
        )

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
