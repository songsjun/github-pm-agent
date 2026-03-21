from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from github_pm_agent.models import Event
from github_pm_agent.workflow_instance import WorkflowInstance
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
    def __init__(self, actions: Any, runtime_dir: Optional[Path] = None) -> None:
        self.actions = actions
        self.runtime_dir: Path = runtime_dir or Path(tempfile.mkdtemp())
        self.process_calls = 0
        self.run_ai_handler_calls: List[Dict[str, Any]] = []
        self.run_raw_text_handler_calls: List[Dict[str, Any]] = []
        self.role_registry: Any = None

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

    def run_raw_text_handler(self, event: Event, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.run_raw_text_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
        return {"raw_text": f"output for {role}"}


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


def _discussion_event(**metadata: Any) -> Event:
    return Event(
        event_id="evt-disc-1",
        event_type="discussion",
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/discussions/5",
        title="Feature idea",
        body="Let's brainstorm",
        target_kind="discussion",
        target_number=5,
        metadata=dict(metadata),
    )


def test_phase_workflow_auto_chains_to_issue_breakdown() -> None:
    """After requirements completes (no gate), orchestrator auto-chains to issue_breakdown and creates issues."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance at requirements phase (gate already cleared by prior advance)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("requirements")

        issue_json = '[{"title": "Task 1", "body": "desc", "labels": ["enhancement"]}]'

        class FakeEngineWithIssueBreakdown(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
                if "issue_breakdown" in prompt_path:
                    return {"raw_text": issue_json}
                return {"raw_text": f"output for {role}"}

        actions = RecordingActions()
        engine = FakeEngineWithIssueBreakdown(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        # Two AI calls: requirements + issue_breakdown
        assert len(engine.run_raw_text_handler_calls) == 2
        phases_called = [c["prompt_path"] for c in engine.run_raw_text_handler_calls]
        assert any("requirements" in p for p in phases_called)
        assert any("issue_breakdown" in p for p in phases_called)

        # One create_issue call for the parsed issue (no gate issue created)
        assert len(actions.create_issue_calls) == 1
        assert actions.create_issue_calls[0]["title"] == "Task 1"

        # Result should have created_issues with 1 item
        assert len(result.get("created_issues", [])) == 1
        assert result.get("issue_creation_error", "") == ""

        # Instance should be marked completed
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.is_completed() is True


def test_phase_workflow_skips_when_completed() -> None:
    """A fully completed workflow must skip all processing when triggered again."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance already marked completed
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("issue_breakdown")
        instance.set_completed()

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        assert result.get("skipped") is True
        assert result.get("reason") == "workflow_completed"
        assert engine.run_raw_text_handler_calls == [], "AI handler must not be invoked when workflow is complete"
        assert actions.create_issue_calls == [], "no issues should be created when workflow is complete"


def test_create_issues_from_artifact_fails_loudly_on_bad_json() -> None:
    """When issue_breakdown returns non-JSON, issue_creation_error is set and no issues are created."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance at issue_breakdown phase
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("issue_breakdown")
        instance.set_artifact("requirements", "some requirements text")

        class FakeEngineBadJson(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
                return {"raw_text": "not valid json"}

        actions = RecordingActions()
        engine = FakeEngineBadJson(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        assert result.get("issue_creation_error", "") != "", "error should be set for bad JSON"
        assert result.get("created_issues", []) == [], "no issues should be created"
        assert actions.create_issue_calls == [], "create_issue must not be called"


def test_phase_workflow_skips_when_gate_already_open() -> None:
    """Re-polling a discussion with an open gate must not re-run the phase or create a new gate issue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create the workflow instance with gate_issue_number=42 already set
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("brainstorm")
        instance.set_gate(42, "requirements")

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        assert result.get("skipped") is True
        assert result.get("reason") == "gate_already_open"
        assert result.get("gate_issue_number") == 42
        assert actions.create_issue_calls == [], "no new issue should be created when gate is already open"
        assert engine.run_raw_text_handler_calls == [], "AI handler must not be invoked when skipping"
