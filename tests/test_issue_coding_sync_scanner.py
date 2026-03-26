from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from github_pm_agent.issue_coding_sync_scanner import IssueCodingSyncScanner
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.workflow_instance import WorkflowInstance


class RecordingActions:
    def __init__(self) -> None:
        self.remove_label_calls: List[Dict[str, Any]] = []
        self.add_label_calls: List[Dict[str, Any]] = []

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        payload = {"number": number, "labels": list(labels)}
        self.remove_label_calls.append(payload)
        return payload

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        payload = {"number": number, "labels": list(labels)}
        self.add_label_calls.append(payload)
        return payload


class FakeClient:
    def __init__(self, responses: Dict[str, Any]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
        self.calls.append({"path": path, "params": params, "method": method})
        if method == "PATCH" and isinstance(params, dict):
            self.responses[path] = {**dict(self.responses.get(path, {})), **params}
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
        "title": "Implement SelectedPlace schema parsing",
        "body": "Issue body",
        "target_kind": "issue",
        "target_number": 42,
        "metadata": {},
    }


def test_issue_coding_sync_scanner_marks_merged_pr_complete() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("pm_decision")
        instance.set_gate(42, "pm_decision", posted_at="2026-03-20T12:00:00Z", resume_mode="execute_action")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.add_pending_comment("stale")
        instance.set_terminated("temporary local failure")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "closed",
                    "merged_at": "2026-03-20T12:30:00Z",
                }
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        results = scanner.scan_and_sync()

        assert results == [
            {
                "repo": "songsjun/example",
                "issue_number": 42,
                "pr_number": 17,
                "phase": "pm_decision",
                "synced_state": "completed_from_merged_pr",
            }
        ]
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.is_completed() is True
        assert reloaded.is_terminated() is False
        assert reloaded.get_gate_issue_number() is None
        assert reloaded.get_pending_comments() == []
        assert actions.remove_label_calls == [{"number": 42, "labels": ["ready-to-code"]}]


def test_issue_coding_sync_scanner_ignores_open_pr() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("pm_decision")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "merged_at": None,
                }
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        results = scanner.scan_and_sync()

        assert results == []
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.is_completed() is False
        assert actions.remove_label_calls == []


def test_issue_coding_sync_scanner_closes_open_pr_after_workflow_failure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("fix_iteration")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_terminated("Fix tests failed at round 0")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "merged_at": None,
                }
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        results = scanner.scan_and_sync()

        assert results == [
            {
                "repo": "songsjun/example",
                "issue_number": 42,
                "pr_number": 17,
                "phase": "fix_iteration",
                "synced_state": "closed_open_pr_after_workflow_failure",
            }
        ]
        assert any(
            call["method"] == "PATCH"
            and call["path"] == "repos/songsjun/example/pulls/17"
            and call["params"] == {"state": "closed"}
            for call in client.calls
        )
        assert actions.remove_label_calls == [{"number": 42, "labels": ["ready-to-code"]}]
        assert actions.add_label_calls == []


def test_issue_coding_sync_scanner_keeps_pr_open_for_gate_limit_termination() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("pm_decision")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_terminated("Phase `pm_decision` exceeded the automatic gate limit (3 attempt(s)).")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "merged_at": None,
                }
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        assert scanner.scan_and_sync() == []
        assert actions.remove_label_calls == []
        assert actions.add_label_calls == []
        assert not any(call["method"] == "PATCH" for call in client.calls)


def test_issue_coding_sync_scanner_keeps_pr_open_for_manual_review_handoff() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("code_review")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_terminated("Code review output was not machine-verifiable")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "merged_at": None,
                }
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        assert scanner.scan_and_sync() == []
        assert actions.remove_label_calls == []
        assert actions.add_label_calls == []
        assert not any(call["method"] == "PATCH" for call in client.calls)


def test_issue_coding_sync_scanner_resumes_terminated_open_pr_from_repo_state() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("implement")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("code_review", "LGTM — no issues found.")
        instance.set_artifact("test_result", '{"passed": true, "summary": "Tests PASSED"}')
        instance.set_terminated("Coding session error: git push rejected")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "open",
                    "merged_at": None,
                    "mergeable": True,
                    "mergeable_state": "clean",
                }
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        results = scanner.scan_and_sync()

        assert results == [
            {
                "repo": "songsjun/example",
                "issue_number": 42,
                "pr_number": 17,
                "phase": "code_review",
                "synced_state": "resumed_from_open_pr",
            }
        ]
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.is_terminated() is False
        assert reloaded.get_phase() == "code_review"
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].event_type == "issue_coding"
        assert pending[0].metadata["advance_to_phase"] == "code_review"
        assert actions.remove_label_calls == []
        assert actions.add_label_calls == []
        assert not any(call["method"] == "PATCH" for call in client.calls)


def test_issue_coding_sync_scanner_restarts_terminated_issue_once_after_closed_pr() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("fix_iteration")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("pr_url", "https://example.test/pull/17")
        instance.set_artifact("branch_name", "ai/issue-42-selected-place")
        instance.set_artifact("test_failure_context", "Summary: expected foo")
        instance.set_terminated("Fix tests failed at round 2")

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "state": "closed",
                    "merged_at": None,
                    "mergeable": False,
                    "mergeable_state": "dirty",
                },
                "repos/songsjun/example/issues/42": {
                    "state": "open",
                },
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        results = scanner.scan_and_sync()

        assert results == [
            {
                "repo": "songsjun/example",
                "issue_number": 42,
                "pr_number": 17,
                "phase": "implement",
                "synced_state": "restarted_from_terminated_workflow",
            }
        ]
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.is_terminated() is False
        assert reloaded.get_phase() == "implement"
        assert reloaded.get_auto_restart_count() == 1
        assert reloaded.get_artifacts() == {"test_failure_context": "Summary: expected foo"}
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].metadata["advance_to_phase"] == "implement"
        assert pending[0].metadata["retry_branch_suffix"] == "-retry-1"
        assert actions.add_label_calls == [{"number": 42, "labels": ["ready-to-code"]}]


def test_issue_coding_sync_scanner_does_not_restart_after_retry_budget_exhausted() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        queue = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_workflow_type("issue_coding")
        instance.set_phase("fix_iteration")
        instance.set_original_event(_issue_coding_event_dict())
        instance.set_terminated("Fix tests failed at round 2")
        instance.increment_auto_restart_count()

        actions = RecordingActions()
        client = FakeClient(
            {
                "repos/songsjun/example/issues/42": {
                    "state": "open",
                },
            }
        )
        scanner = IssueCodingSyncScanner(queue, client, actions)

        assert scanner.scan_and_sync() == []
        assert queue.list_pending() == []
        assert actions.add_label_calls == []
