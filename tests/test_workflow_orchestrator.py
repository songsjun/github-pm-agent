from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from github_pm_agent.models import Event
from github_pm_agent.workflow_orchestrator import WorkflowOrchestrator


class RecordingActions:
    def __init__(self) -> None:
        self.dry_run = True
        self.comment_calls: List[Dict[str, Any]] = []
        self.create_issue_calls: List[Dict[str, Any]] = []

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        payload = {
            "target_kind": target_kind,
            "target_number": target_number,
            "message": message,
            "dry_run": self.dry_run,
        }
        self.comment_calls.append(payload)
        return payload

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        payload = {
            "discussion_id": discussion_id,
            "target_number": number,
            "message": message,
            "dry_run": self.dry_run,
        }
        self.comment_calls.append(payload)
        return payload

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return {"target_number": number, "labels": labels, "dry_run": self.dry_run}

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return {"target_number": number, "labels": labels, "dry_run": self.dry_run}

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = {"title": title, "body": body, "labels": list(labels or []), "dry_run": self.dry_run}
        self.create_issue_calls.append(payload)
        return payload


class FakeEngine:
    def __init__(self, actions: Any) -> None:
        self.actions = actions
        self.process_calls = 0
        self.run_ai_handler_calls: List[Dict[str, Any]] = []

    def process(self, event: Event) -> Dict[str, Any]:
        self.process_calls += 1
        raw = self.actions.comment(event.target_kind, event.target_number, f"reply for {event.event_type}")
        return {
            "plan": {
                "should_act": True,
                "action_type": "comment",
                "target": {"kind": event.target_kind, "number": event.target_number or 0},
            },
            "action": {"executed": True, "action_type": "comment", "raw": raw},
        }

    def run_ai_handler(self, event: Event, prompt_path: str, role: str = "pm") -> Dict[str, Any]:
        self.run_ai_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
        return {"role": role}


class FakeClient:
    def __init__(self, responses: Dict[str, Any]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def api(self, path: str, params: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        self.calls.append({"path": path, "params": params, "method": method})
        return self.responses.get(path, [])


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _event(**metadata: Any) -> Event:
    return Event(
        event_id="evt-1",
        event_type="pull_request_changed",
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/pr/17",
        title="Update feature",
        body="body",
        target_kind="pull_request",
        target_number=17,
        metadata={"head_sha": "abc123", **metadata},
    )


def test_load_workflow_by_event_type() -> None:
    actions = RecordingActions()
    orchestrator = WorkflowOrchestrator(_project_root(), FakeEngine(actions), actions, FakeClient({}), {})

    workflow = orchestrator._load_workflow("pull_request_changed")

    assert workflow["event_type"] == "pull_request_changed"
    assert workflow["signals"][0]["type"] == "ci_checks"
    assert "conditions_by_role" in workflow


def test_load_workflow_fallback() -> None:
    actions = RecordingActions()
    orchestrator = WorkflowOrchestrator(_project_root(), FakeEngine(actions), actions, FakeClient({}), {})

    workflow = orchestrator._load_workflow("unknown_event")

    assert workflow["event_type"] == "default"
    assert workflow["participants"][0]["role"] == "pm"
    assert workflow["signals"] == []


def test_observe_mode_skips_actions() -> None:
    actions = RecordingActions()
    engine = FakeEngine(actions)
    orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

    result = orchestrator._execute_participant(_event(), {"role": "pm", "action_mode": "observe", "priority": 1})

    assert engine.process_calls == 1
    assert actions.comment_calls == []
    assert result["action"]["raw"]["skipped"] is True
    assert result["action"]["raw"]["dry_run"] is True


def test_escalate_idempotency() -> None:
    actions = RecordingActions()
    client = FakeClient(
        {
            "repos/songsjun/example/issues?labels=agent-escalate&state=open": [
                {"title": "[Agent ESCALATE] songsjun/example#17:pull_request_changed:ci_checks"}
            ]
        }
    )
    orchestrator = WorkflowOrchestrator(_project_root(), FakeEngine(actions), actions, client, {})

    orchestrator._escalate(_event(), "ci_checks", "detail")

    assert actions.create_issue_calls == []


def test_signals_pass() -> None:
    actions = RecordingActions()
    client = FakeClient(
        {
            "repos/songsjun/example/commits/abc123/check-runs": {
                "check_runs": [{"name": "test", "status": "completed", "conclusion": "success"}]
            },
            "repos/songsjun/example/pulls/17/reviews": [{"user": {"login": "reviewer1"}, "state": "APPROVED"}],
        }
    )
    engine = FakeEngine(actions)
    orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, client, {})

    result = orchestrator.process(_event())

    assert result["signal_failures"] == []
    assert result["escalated"] is False
    assert actions.create_issue_calls == []


def test_signals_fail_ci() -> None:
    actions = RecordingActions()
    client = FakeClient(
        {
            "repos/songsjun/example/commits/abc123/check-runs": {
                "check_runs": [{"name": "test", "status": "completed", "conclusion": "failure"}]
            },
            "repos/songsjun/example/pulls/17/reviews": [{"user": {"login": "reviewer1"}, "state": "APPROVED"}],
            "repos/songsjun/example/issues?labels=agent-escalate&state=open": [],
        }
    )
    engine = FakeEngine(actions)
    orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, client, {})

    result = orchestrator.process(_event())

    assert len(result["signal_failures"]) == 1
    assert result["signal_failures"][0]["type"] == "ci_checks"
    assert len(actions.create_issue_calls) == 1
    assert actions.create_issue_calls[0]["labels"] == ["agent-escalate"]
