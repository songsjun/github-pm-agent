from __future__ import annotations

from typing import Any, Dict, List

from github_pm_agent.queue_store import enqueue_pending_payload
from github_pm_agent.utils import build_requeued_event
from github_pm_agent.workflow_instance import WorkflowInstance


class IssueCodingRecoveryScanner:
    """Repair active issue-coding workflows that lost their pending resume event."""

    def __init__(self, queue: Any, default_branch: str = "main") -> None:
        self.queue = queue
        self.default_branch = default_branch

    def scan_and_requeue(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        pending_events = self.queue.list_pending()
        pending_keys = {
            (event.repo, event.target_number, event.event_type)
            for event in pending_events
            if event.target_number is not None
        }

        results: List[Dict[str, Any]] = []
        for state_path in workflows_dir.glob("*/*/state.json"):
            repo = state_path.parts[-3].replace("__", "/", 1)
            issue_number = int(state_path.parts[-2])
            instance = WorkflowInstance(state_path)

            if instance.is_completed() or instance.is_terminated():
                continue
            if instance.get_workflow_type() != "issue_coding":
                continue
            if instance.get_gate_next_phase() or instance.is_awaiting_clarification():
                continue

            phase = instance.get_phase() or ""
            if not phase:
                continue

            original_event = instance.get_original_event()
            if not original_event:
                continue

            workflow_key = (repo, issue_number, str(original_event.get("event_type") or "issue_coding"))
            if workflow_key in pending_keys:
                continue

            resumed_event = self._build_resumed_event(original_event, instance.get_artifacts(), phase)
            if not enqueue_pending_payload(self.queue.runtime_dir, resumed_event):
                continue

            pending_keys.add(workflow_key)
            results.append(
                {
                    "repo": repo,
                    "issue_number": issue_number,
                    "phase": phase,
                    "reason": "missing_pending_resume",
                    "event_id": resumed_event["event_id"],
                }
            )

        return results

    def _build_resumed_event(
        self,
        original_event: Dict[str, Any],
        artifacts: Dict[str, Any],
        phase: str,
    ) -> Dict[str, Any]:
        metadata = dict(original_event.get("metadata", {}))
        metadata["advance_to_phase"] = phase
        metadata["artifacts"] = artifacts
        if phase == "merge_conflict_resolution":
            metadata.setdefault(
                "gate_human_comment",
                (
                    f"PR branch may be out of date with `{self.default_branch}`. "
                    "If the branch no longer merges cleanly, update it on the latest base branch, "
                    "resolve conflicts, rerun tests, and return to review."
                ),
            )
            metadata.setdefault("gate_response_type", "workflow_recovery")

        return build_requeued_event(
            original_event,
            metadata=metadata,
            reason="workflow_recovery",
        )
