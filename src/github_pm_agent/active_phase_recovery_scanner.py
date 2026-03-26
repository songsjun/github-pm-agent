from __future__ import annotations

from typing import Any, Dict, List, Tuple

from github_pm_agent.queue_store import enqueue_pending_payload
from github_pm_agent.utils import build_requeued_event
from github_pm_agent.workflow_instance import WorkflowInstance


class ActivePhaseRecoveryScanner:
    """Repair active workflows that lost their event while the current phase artifact is still missing."""

    def __init__(self, queue: Any) -> None:
        self.queue = queue

    def scan_and_requeue(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        pending_events = self.queue.list_pending()
        pending_keys: set[Tuple[str, int, str]] = {
            (event.repo, event.target_number, event.event_type)
            for event in pending_events
            if event.target_number is not None
        }

        results: List[Dict[str, Any]] = []
        for state_path in workflows_dir.glob("*/*/state.json"):
            repo = state_path.parts[-3].replace("__", "/", 1)
            target_number = int(state_path.parts[-2])
            instance = WorkflowInstance(state_path)

            if instance.is_completed() or instance.is_terminated():
                continue
            if instance.get_gate_next_phase() or instance.is_awaiting_clarification():
                continue

            phase = instance.get_phase() or ""
            if not phase:
                continue

            artifacts = instance.get_artifacts()
            if phase in artifacts:
                continue

            original_event = instance.get_original_event()
            if not original_event:
                continue

            workflow_key = (repo, target_number, str(original_event.get("event_type") or ""))
            if workflow_key in pending_keys:
                continue

            resumed_event = build_requeued_event(
                original_event,
                metadata={
                    **dict(original_event.get("metadata", {})),
                    "advance_to_phase": phase,
                    "artifacts": artifacts,
                },
                reason="active_phase_recovery",
            )
            if not enqueue_pending_payload(self.queue.runtime_dir, resumed_event):
                continue

            pending_keys.add(workflow_key)
            results.append(
                {
                    "repo": repo,
                    "target_number": target_number,
                    "workflow_type": instance.get_workflow_type() or "",
                    "phase": phase,
                    "reason": "missing_phase_artifact",
                    "event_id": resumed_event["event_id"],
                }
            )

        return results
