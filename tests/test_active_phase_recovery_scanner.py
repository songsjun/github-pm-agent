from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from github_pm_agent.active_phase_recovery_scanner import ActivePhaseRecoveryScanner
from github_pm_agent.models import Event
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.workflow_instance import WorkflowInstance


def _discussion_event_dict() -> Dict[str, Any]:
    return {
        "event_id": "evt-discussion-1",
        "event_type": "discussion",
        "source": "test",
        "occurred_at": "2026-03-20T00:00:00Z",
        "repo": "songsjun/example",
        "actor": "songsjun",
        "url": "https://example.test/discussions/1",
        "title": "Weather Atlas MVP",
        "body": "Discussion body",
        "target_kind": "discussion",
        "target_number": 1,
        "metadata": {"node_id": "D_discussion_1"},
    }


def test_active_phase_recovery_scanner_requeues_discussion_phase_with_missing_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 1)
        instance.set_workflow_type("discussion")
        instance.set_phase("issue_breakdown")
        instance.set_original_event(_discussion_event_dict())
        instance.set_artifact("tech_review", "done")

        scanner = ActivePhaseRecoveryScanner(queue)

        results = scanner.scan_and_requeue()

        assert len(results) == 1
        assert results[0]["workflow_type"] == "discussion"
        assert results[0]["phase"] == "issue_breakdown"
        resumed = queue.pop()
        assert resumed is not None
        assert resumed.event_type == "discussion"
        assert resumed.target_number == 1
        assert resumed.metadata["advance_to_phase"] == "issue_breakdown"
        assert resumed.metadata["artifacts"]["tech_review"] == "done"
        assert resumed.metadata["_queue"]["requeued_from"] == "active_phase_recovery"


def test_active_phase_recovery_scanner_skips_workflow_with_pending_event() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 1)
        instance.set_workflow_type("discussion")
        instance.set_phase("issue_breakdown")
        instance.set_original_event(_discussion_event_dict())

        queue.enqueue(
            [
                Event(
                    event_id="evt-pending-1",
                    event_type="discussion",
                    source="test",
                    occurred_at="2026-03-20T01:00:00Z",
                    repo="songsjun/example",
                    actor="songsjun",
                    url="https://example.test/discussions/1",
                    title="Weather Atlas MVP",
                    body="Discussion body",
                    target_kind="discussion",
                    target_number=1,
                    metadata={"advance_to_phase": "issue_breakdown"},
                )
            ]
        )

        scanner = ActivePhaseRecoveryScanner(queue)

        assert scanner.scan_and_requeue() == []
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].event_id == "evt-pending-1"
