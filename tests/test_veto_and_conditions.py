from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional

from github_pm_agent.engine import EventEngine
from github_pm_agent.models import AiResponse, Event
from github_pm_agent.workflow_orchestrator import WorkflowOrchestrator


class RecordingActions:
    def __init__(self) -> None:
        self.dry_run = True
        self.create_issue_calls: List[Dict[str, Any]] = []

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        return {"target_kind": target_kind, "target_number": target_number, "message": message}

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        return {"discussion_id": discussion_id, "target_number": number, "message": message}

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return {"target_number": number, "labels": list(labels)}

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return {"target_number": number, "labels": list(labels)}

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = {"title": title, "body": body, "labels": list(labels or []), "dry_run": self.dry_run}
        self.create_issue_calls.append(payload)
        return payload


class FakeClient:
    def __init__(self, responses: Dict[str, Any]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def api(self, path: str, params: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        self.calls.append({"path": path, "params": params, "method": method})
        return self.responses.get(path, [])


class FakeWorkflowEngine:
    def __init__(self, actions: RecordingActions, veto_results: Optional[List[Dict[str, Any]]] = None) -> None:
        self.actions = actions
        self.process_calls = 0
        self.veto_calls: List[str] = []
        self._veto_results = list(veto_results or [])

    def process(self, event: Event) -> Dict[str, Any]:
        self.process_calls += 1
        return {
            "plan": {
                "should_act": True,
                "reason": "test execution",
                "action_type": "comment",
                "target": {"kind": event.target_kind, "number": event.target_number or 0},
                "message": "reply",
            },
            "action": {"executed": True, "action_type": "comment", "raw": {"message": "reply"}},
        }

    def run_ai_handler(self, event: Event, prompt_path: str, role: str = "pm") -> Dict[str, Any]:
        return {"role": role, "prompt_path": prompt_path}

    def run_veto_handler(self, event: Event, role: str = "pm") -> Dict[str, Any]:
        self.veto_calls.append(role)
        if self._veto_results:
            return self._veto_results.pop(0)
        return {
            "plan": {"should_act": False, "reason": "no veto", "action_type": "none", "target": {"kind": event.target_kind, "number": event.target_number or 0}, "message": ""},
            "action": {"executed": False, "action_type": "none", "target": {"kind": event.target_kind, "number": event.target_number or 0}, "message": "", "raw": {}},
            "vetoed": False,
            "veto_reason": "",
        }


class FakeAIManager:
    def __init__(self, project_root: Path, content: str) -> None:
        self.project_root = project_root
        self.content = content
        self.requests: List[Any] = []

    def default_provider(self) -> str:
        return "fake"

    def default_model(self, provider_name: str = "") -> str:
        return "fake-model"

    def generate(self, request: Any) -> AiResponse:
        self.requests.append(request)
        return AiResponse(
            provider=request.provider,
            model=request.model,
            content=self.content,
            raw={},
            session_key=request.session_key,
        )


def _event(
    *,
    event_type: str = "pull_request_changed",
    target_kind: str = "pull_request",
    target_number: Optional[int] = 17,
    metadata: Optional[Dict[str, Any]] = None,
) -> Event:
    return Event(
        event_id="evt-1",
        event_type=event_type,
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/item/17",
        title="Test title",
        body="body",
        target_kind=target_kind,
        target_number=target_number,
        metadata=metadata or {},
    )


def _write_workflow(project_root: Path, event_type: str, body: str) -> None:
    workflows_dir = project_root / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / f"{event_type}.yaml").write_text(dedent(body).strip() + "\n", encoding="utf-8")


def test_veto_blocks_subsequent_participants(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "pull_request_changed",
        """
        event_type: pull_request_changed
        participants:
          - role: engineer
            action_mode: respond
            priority: 1
          - role: security
            action_mode: veto
            priority: 2
          - role: pm
            action_mode: respond
            priority: 3
        signals: []
        """,
    )
    actions = RecordingActions()
    engine = FakeWorkflowEngine(
        actions,
        veto_results=[
            {
                "plan": {"should_act": False, "reason": "veto", "action_type": "none", "target": {"kind": "pull_request", "number": 17}, "message": "Block due to secret"},
                "action": {"executed": False, "action_type": "none", "target": {"kind": "pull_request", "number": 17}, "message": "Block due to secret", "raw": {}},
                "vetoed": True,
                "veto_reason": "Block due to secret",
            }
        ],
    )
    client = FakeClient({"repos/songsjun/example/issues?labels=agent-escalate&state=open": []})
    orchestrator = WorkflowOrchestrator(tmp_path, engine, actions, client, {})

    result = orchestrator.process(_event())

    assert engine.process_calls == 1
    assert engine.veto_calls == ["security"]
    assert len(result["participants"]) == 2
    assert result["workflow"]["vetoed"] is True
    assert result["vetoed"] is True
    assert len(actions.create_issue_calls) == 1
    assert actions.create_issue_calls[0]["body"] == "Block due to secret"


def test_veto_not_triggered(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "pull_request_changed",
        """
        event_type: pull_request_changed
        participants:
          - role: engineer
            action_mode: respond
            priority: 1
          - role: security
            action_mode: veto
            priority: 2
          - role: pm
            action_mode: respond
            priority: 3
        signals: []
        """,
    )
    actions = RecordingActions()
    engine = FakeWorkflowEngine(
        actions,
        veto_results=[
            {
                "plan": {"should_act": False, "reason": "clear", "action_type": "none", "target": {"kind": "pull_request", "number": 17}, "message": ""},
                "action": {"executed": False, "action_type": "none", "target": {"kind": "pull_request", "number": 17}, "message": "", "raw": {}},
                "vetoed": False,
                "veto_reason": "",
            }
        ],
    )
    orchestrator = WorkflowOrchestrator(tmp_path, engine, actions, FakeClient({}), {})

    result = orchestrator.process(_event())

    assert engine.process_calls == 2
    assert engine.veto_calls == ["security"]
    assert len(result["participants"]) == 3
    assert result["workflow"]["vetoed"] is False
    assert result["vetoed"] is False
    assert actions.create_issue_calls == []


def test_condition_files_match_skip(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "pull_request_changed",
        """
        event_type: pull_request_changed
        participants:
          - role: security
            action_mode: respond
            priority: 1
            condition:
              files_match: "auth/**|**/.env*"
        signals: []
        """,
    )
    actions = RecordingActions()
    engine = FakeWorkflowEngine(actions)
    client = FakeClient({"repos/songsjun/example/pulls/17/files": [{"filename": "docs/readme.md"}]})
    orchestrator = WorkflowOrchestrator(tmp_path, engine, actions, client, {})

    result = orchestrator.process(_event())

    assert engine.process_calls == 0
    assert result["participants"][0]["result"] == {"skipped": True, "reason": "condition_not_met"}
    assert client.calls[0]["path"] == "repos/songsjun/example/pulls/17/files"


def test_condition_files_match_execute(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "pull_request_changed",
        """
        event_type: pull_request_changed
        participants:
          - role: security
            action_mode: respond
            priority: 1
            condition:
              files_match: "auth/**|**/.env*"
        signals: []
        """,
    )
    actions = RecordingActions()
    engine = FakeWorkflowEngine(actions)
    client = FakeClient({"repos/songsjun/example/pulls/17/files": [{"filename": "auth/login.py"}]})
    orchestrator = WorkflowOrchestrator(tmp_path, engine, actions, client, {})

    result = orchestrator.process(_event())

    assert engine.process_calls == 1
    assert result["participants"][0]["result"]["action"]["executed"] is True


def test_condition_labels_contain(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "issue_changed",
        """
        event_type: issue_changed
        participants:
          - role: pm
            action_mode: respond
            priority: 1
            condition:
              labels_contain:
                - security
        signals: []
        """,
    )
    actions = RecordingActions()
    engine = FakeWorkflowEngine(actions)
    orchestrator = WorkflowOrchestrator(tmp_path, engine, actions, FakeClient({}), {})

    result = orchestrator.process(_event(event_type="issue_changed", target_kind="issue", metadata={"labels": ["bug"]}))

    assert engine.process_calls == 0
    assert result["participants"][0]["result"] == {"skipped": True, "reason": "condition_not_met"}


def test_veto_fail_open(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parent.parent
    actions = RecordingActions()
    ai_manager = FakeAIManager(project_root, "definitely not json")
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    engine = EventEngine({}, ai_manager, actions, runtime_dir)

    result = engine.run_veto_handler(_event())

    assert result["vetoed"] is False
    assert result["veto_reason"] == ""
    assert result["action"]["executed"] is False
    assert ai_manager.requests[0].prompt_path == "prompts/actions/veto_check.md"
