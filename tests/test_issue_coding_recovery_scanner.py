from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from github_pm_agent.issue_coding_recovery_scanner import IssueCodingRecoveryScanner
from github_pm_agent.models import Event
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.workflow_instance import WorkflowInstance


def _issue_coding_event_dict() -> Dict[str, Any]:
    return {
        "event_id": "evt-issue-coding-1",
        "event_type": "issue_coding",
        "source": "test",
        "occurred_at": "2026-03-20T00:00:00Z",
        "repo": "songsjun/example",
        "actor": "alice",
        "url": "https://example.test/issues/42",
        "title": "Implement comparison store tests",
        "body": "Issue body",
        "target_kind": "issue",
        "target_number": 42,
        "metadata": {},
    }


def test_issue_coding_recovery_scanner_requeues_orphaned_active_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("fix_iteration")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("test_result", '{"passed": false}')
        instance.set_last_merge_conflict_signature("17:head:dirty")

        scanner = IssueCodingRecoveryScanner(queue, default_branch="main")

        results = scanner.scan_and_requeue()

        assert len(results) == 1
        assert results[0]["issue_number"] == 42
        assert results[0]["phase"] == "fix_iteration"
        resumed = queue.pop()
        assert resumed is not None
        assert resumed.event_type == "issue_coding"
        assert resumed.target_number == 42
        assert resumed.metadata["advance_to_phase"] == "fix_iteration"
        assert resumed.metadata["artifacts"]["pr_number"] == "17"
        assert "no longer merges cleanly" in resumed.metadata["gate_human_comment"] or "out of date" in resumed.metadata["gate_human_comment"]
        assert resumed.metadata["_queue"]["requeued_from"] == "workflow_recovery"


def test_issue_coding_recovery_scanner_skips_workflow_that_already_has_pending_issue_coding_event() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("fix_iteration")
        instance.set_original_event(_issue_coding_event_dict())

        queue.enqueue(
            [
                Event(
                    event_id="evt-pending-1",
                    event_type="issue_coding",
                    source="test",
                    occurred_at="2026-03-20T01:00:00Z",
                    repo="songsjun/example",
                    actor="alice",
                    url="https://example.test/issues/42",
                    title="Implement comparison store tests",
                    body="Issue body",
                    target_kind="issue",
                    target_number=42,
                    metadata={"advance_to_phase": "fix_iteration"},
                )
            ]
        )

        scanner = IssueCodingRecoveryScanner(queue, default_branch="main")

        assert scanner.scan_and_requeue() == []
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].event_id == "evt-pending-1"
