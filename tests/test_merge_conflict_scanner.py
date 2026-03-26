from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from github_pm_agent.merge_conflict_scanner import MergeConflictScanner
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.workflow_instance import WorkflowInstance


class RecordingActions:
    def __init__(self) -> None:
        self.comment_calls: List[Dict[str, Any]] = []

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        payload = {"target_kind": target_kind, "target_number": target_number, "message": message}
        self.comment_calls.append(payload)
        return payload


class FakeClient:
    def __init__(self, responses: Dict[str, Any]) -> None:
        self.responses = responses

    def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
        return self.responses.get(path, {})


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


def test_merge_conflict_scanner_requeues_conflicting_pm_decision_gate() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("pm_decision")
        instance.set_gate(42, "pm_decision", posted_at="2026-03-20T12:00:00Z", resume_mode="execute_action")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "mergeable": False,
                    "mergeable_state": "dirty",
                    "head": {"sha": "head-1"},
                    "base": {"sha": "base-1"},
                }
            }
        )
        scanner = MergeConflictScanner(queue, client, actions, {"github": {"default_branch": "main"}})

        results = scanner.scan_and_requeue()

        assert results == [
            {
                "repo": "songsjun/example",
                "issue_number": 42,
                "pr_number": 17,
                "from_phase": "pm_decision",
                "to_phase": "fix_iteration",
                "reason": "merge_conflict",
            }
        ]
        assert any(
            "no longer merges cleanly" in call["message"] for call in actions.comment_calls
        )

        resumed = queue.pop()
        assert resumed is not None
        assert resumed.event_type == "issue_coding"
        assert resumed.metadata["advance_to_phase"] == "fix_iteration"
        assert resumed.metadata["gate_response_type"] == "merge_conflict"

        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.get_gate_issue_number() is None
        assert reloaded.get_last_merge_conflict_signature() == "17:head-1:base-1:dirty"


def test_merge_conflict_scanner_deduplicates_same_conflict_signature() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("code_review")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_last_merge_conflict_signature("17:head-1:base-1:dirty")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "mergeable": False,
                    "mergeable_state": "dirty",
                    "head": {"sha": "head-1"},
                    "base": {"sha": "base-1"},
                }
            }
        )
        scanner = MergeConflictScanner(queue, client, actions, {"github": {"default_branch": "main"}})

        results = scanner.scan_and_requeue()

        assert results == []
        assert actions.comment_calls == []
        assert queue.pop() is None
