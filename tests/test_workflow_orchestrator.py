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
        self.comment_on_discussion_calls: List[Dict[str, Any]] = []
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
            "number": number,
            "message": message,
            "dry_run": self.dry_run,
        }
        self.comment_calls.append(payload)
        self.comment_on_discussion_calls.append({"discussion_id": discussion_id, "number": number, "message": message})
        return payload

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return {"target_number": number, "labels": labels, "dry_run": self.dry_run}

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return {"target_number": number, "labels": labels, "dry_run": self.dry_run}

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = {"title": title, "body": body, "labels": list(labels or []), "dry_run": self.dry_run}
        self.create_issue_calls.append(payload)
        return payload


class NumberedRecordingActions(RecordingActions):
    def __init__(self) -> None:
        super().__init__()
        self._next_issue_number = 100

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = super().create_issue(title, body, labels)
        payload["number"] = self._next_issue_number
        self._next_issue_number += 1
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


def test_phase_workflow_stops_at_tech_review_gate_after_design_evaluation() -> None:
    """After tech_proposal completes, tech_review must evaluate first, then open a gate to issue_breakdown."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance at tech_proposal phase after the prior gate has already advanced.
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("tech_proposal")
        instance.set_artifact("requirements", "some requirements text")
        instance.add_pending_comment("Please include background jobs.")

        issue_json = '[{"title": "Task 1", "body": "desc", "labels": ["enhancement"]}]'
        tech_review_json = '{"decision": "proceed", "docker_compatible": true, "final_design": "use FastAPI", "evaluation_summary": "good", "problem_coverage": []}'

        class FakeEngineWithIssueBreakdown(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append(
                    {"event_id": event.event_id, "prompt_path": prompt_path, "role": role, "variables": dict(variables or {})}
                )
                if "issue_breakdown" in prompt_path:
                    return {"raw_text": issue_json}
                if "tech_review" in prompt_path:
                    return {"raw_text": tech_review_json}
                return {"raw_text": f"output for {role}"}

        actions = NumberedRecordingActions()
        engine = FakeEngineWithIssueBreakdown(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        phases_called = [c["prompt_path"] for c in engine.run_raw_text_handler_calls]
        assert any("tech_proposal" in p for p in phases_called)
        assert any("tech_review" in p for p in phases_called)
        assert not any("issue_breakdown" in p for p in phases_called)

        assert len(actions.create_issue_calls) == 1
        assert actions.create_issue_calls[0]["title"] == "[workflow-gate] songsjun/example Discussion #5 phase=tech_review"
        assert result.get("gate") == {"gate_issue_number": 100, "next_phase": "issue_breakdown"}
        assert result.get("created_issues", []) == []
        assert result.get("issue_creation_error", "") == ""

        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.get_artifacts().get("final_design") == "use FastAPI"
        assert final_instance.get_gate_issue_number() == 100
        assert final_instance.get_gate_next_phase() == "issue_breakdown"
        assert final_instance.get_pending_comments() == []
        assert final_instance.is_completed() is False


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
    """When issue_breakdown returns non-JSON, the workflow must stay retryable and keep pending comments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance at issue_breakdown phase
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("issue_breakdown")
        instance.set_artifact("requirements", "some requirements text")
        instance.set_artifact("final_design", "some final design text")
        instance.add_pending_comment("Keep this comment until the step succeeds.")

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
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.is_completed() is False
        assert final_instance.get_pending_comments() == ["Keep this comment until the step succeeds."]


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


def _discussion_comment_event(**metadata: Any) -> Event:
    return Event(
        event_id="evt-disc-comment-1",
        event_type="discussion_comment",
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/discussions/5",
        title="Feature idea",
        body="Great idea!",
        target_kind="discussion",
        target_number=5,
        metadata=dict(metadata),
    )


def test_record_discussion_comment_active_workflow() -> None:
    """A discussion_comment on an active workflow must be recorded as a pending comment."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance at brainstorm phase (active, not completed)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("brainstorm")

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_comment_event())

        assert result.get("recorded") is True

        # Reload to confirm persistence
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert reloaded.get_pending_comments() == ["Great idea!"]


def test_record_discussion_comment_no_active_workflow() -> None:
    """A discussion_comment with no active workflow must be skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        # No WorkflowInstance created — fresh state

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_comment_event())

        assert result.get("skipped") is True
        assert result.get("reason") == "no_active_workflow"


def test_completion_summary_posted_to_discussion() -> None:
    """After issue_breakdown completes, a completion summary must be posted to the original Discussion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        # Pre-create instance at issue_breakdown phase with requirements artifact
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("issue_breakdown")
        instance.set_artifact("requirements", "some requirements text")

        issue_json = '[{"title": "Task 1", "body": "desc", "labels": []}]'
        tech_review_json = '{"decision": "proceed", "docker_compatible": true, "final_design": "use FastAPI", "evaluation_summary": "good", "problem_coverage": []}'

        class FakeEngineWithIssueBreakdown(FakeEngine):
            def run_raw_text_handler(
                self,
                event: Any,
                prompt_path: str,
                role: str = "pm",
                variables: Optional[Dict[str, Any]] = None,
            ) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append(
                    {"event_id": event.event_id, "prompt_path": prompt_path, "role": role}
                )
                if "issue_breakdown" in prompt_path:
                    return {"raw_text": issue_json}
                if "tech_review" in prompt_path:
                    return {"raw_text": tech_review_json}
                return {"raw_text": f"output for {role}"}

        actions = RecordingActions()
        engine = FakeEngineWithIssueBreakdown(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event(node_id="D_abc123"))

        # Verify completion summary was posted
        assert len(actions.comment_on_discussion_calls) == 1
        call = actions.comment_on_discussion_calls[0]
        assert call["discussion_id"] == "D_abc123"

        # Verify instance state
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.is_completed() is True
        assert final_instance.is_completion_comment_posted() is True


def test_evaluate_design_terminates_on_docker_incompatible() -> None:
    """When tech_review outputs docker_compatible=false, workflow is terminated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("tech_review")
        instance.set_artifact("requirements", "some requirements text")
        instance.set_artifact("tech_proposal_engineer", "some proposal text")

        terminate_json = (
            '{"decision": "proceed", "docker_compatible": false, "final_design": "", '
            '"evaluation_summary": "needs GPU", "problem_coverage": [], "escalation_reason": "requires GPU"}'
        )

        class FakeEngineTerminate(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
                if "tech_review" in prompt_path:
                    return {"raw_text": terminate_json}
                return {"raw_text": f"output for {role}"}

        actions = RecordingActions()
        engine = FakeEngineTerminate(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        assert result.get("terminated") is True
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.is_terminated() is True


def test_evaluate_design_proceeds_and_opens_gate_to_issue_breakdown() -> None:
    """When tech_review outputs proceed, final_design is saved before opening the next gate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("tech_review")
        instance.set_artifact("requirements", "some requirements text")
        instance.set_artifact("tech_proposal_engineer", "some proposal text")

        proceed_json = (
            '{"decision": "proceed", "docker_compatible": true, "final_design": "use FastAPI", '
            '"evaluation_summary": "looks good", "problem_coverage": []}'
        )

        class FakeEngineProceed(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
                if "tech_review" in prompt_path:
                    return {"raw_text": proceed_json}
                return {"raw_text": f"output for {role}"}

        actions = NumberedRecordingActions()
        engine = FakeEngineProceed(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        assert not result.get("terminated")
        assert result.get("gate") == {"gate_issue_number": 100, "next_phase": "issue_breakdown"}
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.get_artifacts().get("final_design") == "use FastAPI"
        assert final_instance.get_gate_issue_number() == 100
        assert final_instance.is_completed() is False


def test_gate_human_comment_is_passed_to_resumed_prompt_variables() -> None:
    """Gate resolution comments must be available to resumed tech_proposal and issue_breakdown prompts."""
    cases = [
        (
            "tech_proposal",
            {"requirements": "some requirements text"},
            "prompts/discussion/tech_proposal.md",
            "output for engineer",
        ),
        (
            "issue_breakdown",
            {"requirements": "some requirements text", "final_design": "use FastAPI"},
            "prompts/discussion/issue_breakdown.md",
            '[{"title": "Task 1", "body": "desc", "labels": ["enhancement"]}]',
        ),
    ]

    for phase, artifacts, expected_prompt, raw_text in cases:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
            instance.set_phase(phase)
            for name, value in artifacts.items():
                instance.set_artifact(name, value)

            class FakeEngineCaptureVariables(FakeEngine):
                def run_raw_text_handler(
                    self,
                    event: Any,
                    prompt_path: str,
                    role: str = "pm",
                    variables: Optional[Dict[str, Any]] = None,
                ) -> Dict[str, Any]:
                    self.run_raw_text_handler_calls.append(
                        {
                            "event_id": event.event_id,
                            "prompt_path": prompt_path,
                            "role": role,
                            "variables": dict(variables or {}),
                        }
                    )
                    return {"raw_text": raw_text}

            actions = RecordingActions()
            engine = FakeEngineCaptureVariables(actions, runtime_dir=runtime_dir)
            orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

            orchestrator.process(_discussion_event(gate_human_comment="Prefer Postgres"))

            first_call = engine.run_raw_text_handler_calls[0]
            assert expected_prompt in first_call["prompt_path"]
            assert first_call["variables"]["human_comment"] == "Human feedback:\nPrefer Postgres\n"


def test_gate_human_comment_placeholders_exist_in_resumed_prompts() -> None:
    assert "$human_comment" in (_project_root() / "prompts/discussion/tech_proposal.md").read_text(encoding="utf-8")
    assert "$human_comment" in (_project_root() / "prompts/discussion/issue_breakdown.md").read_text(encoding="utf-8")


def test_phase_gate_scanner_skips_terminated_instance() -> None:
    """PhaseGateScanner must not re-queue events for terminated workflow instances."""
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("tech_review")
        instance.set_gate(99, "tech_review")
        original_event = {
            "event_id": "evt-disc-1",
            "event_type": "discussion",
            "source": "test",
            "occurred_at": "2026-03-20T00:00:00Z",
            "repo": "songsjun/example",
            "actor": "alice",
            "url": "https://example.test/discussions/5",
            "title": "Feature idea",
            "body": "Let's brainstorm",
            "target_kind": "discussion",
            "target_number": 5,
            "metadata": {},
        }
        instance.set_original_event(original_event)
        instance.set_terminated("test")

        class FakeClientClosed:
            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if "/comments" in path:
                    return []
                if f"/issues/99" in path:
                    return {"state": "closed"}
                return []

        scanner = PhaseGateScanner(store, FakeClientClosed(), "")
        advanced = scanner.scan_and_advance()

        assert advanced == [], "terminated instance must not be re-queued"
        assert store.pop() is None, "nothing should have been enqueued"


def test_phase_gate_scanner_deduplicates_by_repo_and_issue_number() -> None:
    """Advancement records for another repo must not block the same gate number in this repo."""
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore
    from github_pm_agent.utils import append_jsonl

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        append_jsonl(
            runtime_dir / "gate_advanced.jsonl",
            {
                "gate_issue_number": 99,
                "repo": "otherorg/example",
                "discussion_number": 1,
                "from_phase": "tech_review",
                "to_phase": "issue_breakdown",
                "advanced_at": "2026-03-20T00:00:00Z",
            },
        )

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("tech_review")
        instance.set_gate(99, "issue_breakdown")
        instance.set_original_event(
            {
                "event_id": "evt-disc-1",
                "event_type": "discussion",
                "source": "test",
                "occurred_at": "2026-03-20T00:00:00Z",
                "repo": "songsjun/example",
                "actor": "alice",
                "url": "https://example.test/discussions/5",
                "title": "Feature idea",
                "body": "Let's brainstorm",
                "target_kind": "discussion",
                "target_number": 5,
                "metadata": {},
            }
        )

        class FakeClientClosed:
            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if "/comments" in path:
                    return []
                if path == "repos/songsjun/example/issues/99":
                    return {"state": "closed"}
                return []

        scanner = PhaseGateScanner(store, FakeClientClosed(), "")
        advanced = scanner.scan_and_advance()

        assert advanced == [
            {
                "repo": "songsjun/example",
                "discussion_number": 5,
                "from_phase": "tech_review",
                "to_phase": "issue_breakdown",
            }
        ]
        resumed = store.pop()
        assert resumed is not None
        assert resumed.metadata["advance_to_phase"] == "issue_breakdown"
        assert resumed.metadata["gate_human_comment"] == ""
