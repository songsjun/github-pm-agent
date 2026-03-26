from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from github_pm_agent.created_issue_fanout_scanner import CreatedIssueFanoutScanner
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


class FakeClient:
    def __init__(self, issues: Dict[int, Dict[str, Any]]) -> None:
        self.issues = issues

    def api(self, path: str, method: str = "GET", params: Dict[str, Any] | None = None) -> Any:
        issue_number = int(path.rstrip("/").split("/")[-1])
        return self.issues.get(issue_number, {})


def test_created_issue_fanout_scanner_enqueues_missing_issue_coding_events() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 1)
        instance.set_workflow_type("discussion")
        instance.set_original_event(_discussion_event_dict())
        instance.set_created_issue_refs([{"number": 7, "title": "Implement lookup-first search"}])
        instance.set_completed()

        client = FakeClient(
            {
                7: {
                    "number": 7,
                    "state": "open",
                    "title": "Implement lookup-first search",
                    "body": "Issue body",
                    "html_url": "https://example.test/issues/7",
                    "updated_at": "2026-03-20T02:00:00Z",
                    "labels": [{"name": "frontend"}, {"name": "ready-to-code"}],
                    "user": {"login": "songsjun"},
                }
            }
        )
        scanner = CreatedIssueFanoutScanner(queue, client)

        results = scanner.scan_and_enqueue()

        assert len(results) == 1
        assert results[0]["issue_number"] == 7
        event = queue.pop()
        assert event is not None
        assert event.event_type == "issue_coding"
        assert event.target_number == 7
        assert event.metadata["labels"] == ["frontend", "ready-to-code"]


def test_created_issue_fanout_scanner_skips_issue_with_existing_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        discussion = WorkflowInstance.load(runtime_dir, "songsjun/example", 1)
        discussion.set_workflow_type("discussion")
        discussion.set_original_event(_discussion_event_dict())
        discussion.set_created_issue_refs([{"number": 7, "title": "Implement lookup-first search"}])
        discussion.set_completed()

        issue_workflow = WorkflowInstance.load(runtime_dir, "songsjun/example", 7)
        issue_workflow.set_workflow_type("issue_coding")
        issue_workflow.set_phase("implement")

        client = FakeClient(
            {
                7: {
                    "number": 7,
                    "state": "open",
                    "title": "Implement lookup-first search",
                    "body": "Issue body",
                    "html_url": "https://example.test/issues/7",
                    "updated_at": "2026-03-20T02:00:00Z",
                    "labels": [{"name": "frontend"}, {"name": "ready-to-code"}],
                    "user": {"login": "songsjun"},
                }
            }
        )
        scanner = CreatedIssueFanoutScanner(queue, client)

        assert scanner.scan_and_enqueue() == []
        assert queue.pop() is None
