from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from github_pm_agent.queue_store import enqueue_pending_payload
from github_pm_agent.utils import build_requeued_event
from github_pm_agent.workflow_instance import WorkflowInstance


class IssueCodingSyncScanner:
    """Reconcile terminated issue-coding workflows with current repo state."""

    MAX_AUTO_RESTARTS = 1
    MAX_OPEN_PR_RECOVERIES = 2

    def __init__(self, queue: Any, client: Any, actions: Any) -> None:
        self.queue = queue
        self.client = client
        self.actions = actions

    def scan_and_sync(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        pending_keys = {
            (event.repo, event.target_number, event.event_type)
            for event in self.queue.list_pending()
            if event.target_number is not None
        }

        results: List[Dict[str, Any]] = []
        for state_path in workflows_dir.glob("*/*/state.json"):
            repo = state_path.parts[-3].replace("__", "/", 1)
            issue_number = int(state_path.parts[-2])
            instance = WorkflowInstance(state_path)

            if instance.get_workflow_type() != "issue_coding":
                continue

            pr_number = self._artifact_pr_number(instance)
            pr_state = self._load_pull_request_state(repo, pr_number) if pr_number is not None else {}

            if self._pull_request_is_merged(pr_state):
                synced = self._sync_completed_from_merged_pr(instance, issue_number, pr_number, repo)
                if synced:
                    results.append(synced)
                continue

            if instance.is_completed():
                continue

            if instance.is_terminated():
                resumed = self._resume_terminated_open_pr(
                    instance,
                    repo=repo,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    pr_state=pr_state,
                    pending_keys=pending_keys,
                )
                if resumed:
                    pending_keys.add((repo, issue_number, "issue_coding"))
                    results.append(resumed)
                    continue

                closed = self._close_failed_open_pr(
                    instance,
                    repo=repo,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    pr_state=pr_state,
                )
                if closed:
                    results.append(closed)
                    pr_state = self._load_pull_request_state(repo, pr_number) if pr_number is not None else {}

                restarted = self._restart_terminated_workflow(
                    instance,
                    repo=repo,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    pr_state=pr_state,
                    pending_keys=pending_keys,
                )
                if restarted:
                    pending_keys.add((repo, issue_number, "issue_coding"))
                    results.append(restarted)
                continue

        return results

    @staticmethod
    def _artifact_pr_number(instance: WorkflowInstance) -> Optional[int]:
        raw_pr = instance.get_artifacts().get("pr_number", "")
        if not str(raw_pr).strip().isdigit():
            return None
        return int(str(raw_pr))

    def _load_pull_request_state(self, repo: str, pr_number: int | None) -> Dict[str, Any]:
        if pr_number is None:
            return {}
        try:
            payload = self.client.api(f"repos/{repo}/pulls/{pr_number}", method="GET")
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_issue_state(self, repo: str, issue_number: int) -> Dict[str, Any]:
        try:
            payload = self.client.api(f"repos/{repo}/issues/{issue_number}", method="GET")
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _pull_request_is_merged(pr_state: Dict[str, Any]) -> bool:
        merged_at = pr_state.get("merged_at") or pr_state.get("mergedAt")
        merged = pr_state.get("merged")
        return bool(merged_at) or merged is True

    @staticmethod
    def _pull_request_is_open(pr_state: Dict[str, Any]) -> bool:
        return str(pr_state.get("state") or "").strip().lower() == "open"

    @staticmethod
    def _pull_request_has_merge_conflict(pr_state: Dict[str, Any]) -> bool:
        mergeable_state = str(pr_state.get("mergeable_state") or "").strip().lower()
        mergeable = pr_state.get("mergeable")
        return mergeable_state == "dirty" or mergeable is False

    @staticmethod
    def _load_test_result_artifact(instance: WorkflowInstance) -> Dict[str, Any]:
        raw = instance.get_artifacts().get("test_result") or {}
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _sync_completed_from_merged_pr(
        self,
        instance: WorkflowInstance,
        issue_number: int,
        pr_number: int | None,
        repo: str,
    ) -> Optional[Dict[str, Any]]:
        if pr_number is None:
            return None
        if instance.get_gate_issue_number() or instance.get_discussion_gate_node_id():
            instance.clear_gate()
        if instance.get_pending_comments():
            instance.clear_pending_comments()
        if instance.is_terminated():
            instance.clear_terminated()
        if not instance.is_completion_comment_posted():
            instance.set_completion_comment_posted()
        instance.set_completed()
        try:
            self.actions.remove_labels(issue_number, ["ready-to-code"])
        except Exception:
            pass
        return {
            "repo": repo,
            "issue_number": issue_number,
            "pr_number": pr_number,
            "phase": instance.get_phase(),
            "synced_state": "completed_from_merged_pr",
        }

    def _resume_terminated_open_pr(
        self,
        instance: WorkflowInstance,
        *,
        repo: str,
        issue_number: int,
        pr_number: int | None,
        pr_state: Dict[str, Any],
        pending_keys: set[tuple[str, int, str]],
    ) -> Optional[Dict[str, Any]]:
        if pr_number is None or not self._pull_request_is_open(pr_state):
            return None
        if (repo, issue_number, "issue_coding") in pending_keys:
            return None
        if instance.get_open_pr_recovery_count() >= self.MAX_OPEN_PR_RECOVERIES:
            return None

        test_result = self._load_test_result_artifact(instance)
        if not bool(test_result.get("passed")):
            return None

        target_phase = "merge_conflict_resolution" if self._pull_request_has_merge_conflict(pr_state) else "code_review"
        original_event = instance.get_original_event()
        if not original_event:
            return None

        recovery_count = instance.increment_open_pr_recovery_count()
        instance.clear_terminated()
        if instance.get_gate_issue_number() or instance.get_discussion_gate_node_id():
            instance.clear_gate()
        if instance.is_awaiting_clarification():
            instance.clear_clarification()
        if instance.get_pending_comments():
            instance.clear_pending_comments()
        instance.set_phase(target_phase)

        recovery_note = (
            f"Recovered terminated workflow from live PR #{pr_number}. "
            f"Resuming at `{target_phase}` based on current repository state."
        )
        metadata = dict(original_event.get("metadata", {}))
        metadata["advance_to_phase"] = target_phase
        metadata["artifacts"] = instance.get_artifacts()
        metadata["gate_human_comment"] = recovery_note
        metadata["gate_response_type"] = "repo_state_sync"
        metadata["open_pr_recovery_count"] = recovery_count

        if not enqueue_pending_payload(
            self.queue.runtime_dir,
            build_requeued_event(
                original_event,
                metadata=metadata,
                reason="open_pr_repo_state_recovery",
            ),
        ):
            return None

        return {
            "repo": repo,
            "issue_number": issue_number,
            "pr_number": pr_number,
            "phase": target_phase,
            "synced_state": "resumed_from_open_pr",
        }

    def _close_failed_open_pr(
        self,
        instance: WorkflowInstance,
        *,
        repo: str,
        issue_number: int,
        pr_number: int | None,
        pr_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if pr_number is None or not self._pull_request_is_open(pr_state):
            return None
        if not self._should_close_terminated_pr(pr_state, instance.get_terminated_reason()):
            return None
        try:
            self.client.api(
                f"repos/{repo}/pulls/{pr_number}",
                {"state": "closed"},
                method="PATCH",
            )
        except Exception:
            return None
        try:
            self.actions.remove_labels(issue_number, ["ready-to-code"])
        except Exception:
            pass
        return {
            "repo": repo,
            "issue_number": issue_number,
            "pr_number": pr_number,
            "phase": instance.get_phase(),
            "synced_state": "closed_open_pr_after_workflow_failure",
        }

    def _restart_terminated_workflow(
        self,
        instance: WorkflowInstance,
        *,
        repo: str,
        issue_number: int,
        pr_number: int | None,
        pr_state: Dict[str, Any],
        pending_keys: set[tuple[str, int, str]],
    ) -> Optional[Dict[str, Any]]:
        if (repo, issue_number, "issue_coding") in pending_keys:
            return None
        if instance.get_auto_restart_count() >= self.MAX_AUTO_RESTARTS:
            return None
        if not self._should_restart_terminated_issue(instance, repo, issue_number, pr_state):
            return None

        original_event = instance.get_original_event()
        if not original_event:
            return None

        retry_count = instance.increment_auto_restart_count()
        retry_context = self._restart_context(instance, retry_count)
        sanitized_artifacts = self._restart_artifacts(instance)

        instance.clear_terminated()
        if instance.get_gate_issue_number() or instance.get_discussion_gate_node_id():
            instance.clear_gate()
        if instance.is_awaiting_clarification():
            instance.clear_clarification()
        if instance.get_pending_comments():
            instance.clear_pending_comments()
        instance.clear_last_merge_conflict_signature()
        instance.set_review_round(0)
        instance.replace_artifacts(sanitized_artifacts)
        instance.set_phase("implement")

        metadata = dict(original_event.get("metadata", {}))
        metadata["advance_to_phase"] = "implement"
        metadata["artifacts"] = sanitized_artifacts
        metadata["gate_human_comment"] = retry_context
        metadata["gate_response_type"] = "auto_restart"
        metadata["retry_branch_suffix"] = f"-retry-{retry_count}"
        metadata["auto_restart_count"] = retry_count

        if not enqueue_pending_payload(
            self.queue.runtime_dir,
            build_requeued_event(
                original_event,
                metadata=metadata,
                reason="terminated_workflow_restart",
            ),
        ):
            return None

        try:
            self.actions.add_labels(issue_number, ["ready-to-code"])
        except Exception:
            pass

        return {
            "repo": repo,
            "issue_number": issue_number,
            "pr_number": pr_number,
            "phase": "implement",
            "synced_state": "restarted_from_terminated_workflow",
        }

    def _should_restart_terminated_issue(
        self,
        instance: WorkflowInstance,
        repo: str,
        issue_number: int,
        pr_state: Dict[str, Any],
    ) -> bool:
        if self._pull_request_is_open(pr_state):
            return False
        issue_state = self._load_issue_state(repo, issue_number)
        if str(issue_state.get("state") or "").strip().lower() != "open":
            return False
        termination_reason = (instance.get_terminated_reason() or "").lower()
        return any(fragment in termination_reason for fragment in self._auto_restart_reason_fragments())

    def _restart_artifacts(self, instance: WorkflowInstance) -> Dict[str, Any]:
        artifacts = instance.get_artifacts()
        restarted: Dict[str, Any] = {}
        failure_context = str(artifacts.get("test_failure_context") or "").strip()
        if failure_context:
            restarted["test_failure_context"] = failure_context
        return restarted

    def _restart_context(self, instance: WorkflowInstance, retry_count: int) -> str:
        reason = instance.get_terminated_reason().strip()
        lines = [
            f"Automatic retry #{retry_count} after the previous workflow terminated.",
            f"Previous termination reason: {reason}" if reason else "Previous attempt terminated without a detailed reason.",
        ]
        failure_context = str(instance.get_artifacts().get("test_failure_context") or "").strip()
        if failure_context:
            lines.append("Use the captured failure context to fix the underlying problem instead of repeating the same plan.")
        lines.append("Treat this as a fresh implementation attempt on a new retry branch.")
        return "\n".join(lines)

    @staticmethod
    def _should_close_terminated_pr(pr_state: Dict[str, Any], termination_reason: str) -> bool:
        if not IssueCodingSyncScanner._pull_request_is_open(pr_state):
            return False
        normalized_reason = (termination_reason or "").lower()
        if any(fragment in normalized_reason for fragment in IssueCodingSyncScanner._manual_followup_reason_fragments()):
            return False
        return any(fragment in normalized_reason for fragment in IssueCodingSyncScanner._auto_close_reason_fragments())

    @staticmethod
    def _manual_followup_reason_fragments() -> tuple[str, ...]:
        return (
            "automatic gate limit",
            "manual intervention",
            "not machine-verifiable",
            "parse failure",
            "iteration error",
        )

    @staticmethod
    def _auto_close_reason_fragments() -> tuple[str, ...]:
        return (
            "tests failed after",
            "fix tests failed",
            "merge conflict resolution failed",
            "code review exceeded",
        )

    @staticmethod
    def _auto_restart_reason_fragments() -> tuple[str, ...]:
        return (
            "tests failed after",
            "fix tests failed",
            "merge conflict resolution failed",
            "code review exceeded",
            "coding session error",
        )
