from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from github_pm_agent.coding_session import TestResult as CodingTestResult
from github_pm_agent.models import Event
from github_pm_agent.workflow_instance import WorkflowInstance
from github_pm_agent.workflow_orchestrator import WorkflowOrchestrator


class RecordingActions:
    def __init__(self) -> None:
        self.dry_run = True
        self.comment_calls: List[Dict[str, Any]] = []
        self.comment_on_discussion_calls: List[Dict[str, Any]] = []
        self.create_issue_calls: List[Dict[str, Any]] = []
        self.coding_session_calls: List[Dict[str, Any]] = []
        self.submit_pr_review_calls: List[Dict[str, Any]] = []
        self.merge_or_reopen_calls: List[Dict[str, Any]] = []

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

    def coding_session(
        self,
        issue_number: int,
        repo: str,
        branch_name: str,
        pr_title: str,
        pr_body: str,
        base_branch: str,
        coding_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {
            "issue_number": issue_number,
            "repo": repo,
            "branch_name": branch_name,
            "pr_title": pr_title,
            "pr_body": pr_body,
            "base_branch": base_branch,
            "coding_result": dict(coding_result),
            "dry_run": self.dry_run,
        }
        self.coding_session_calls.append(payload)
        return payload

    def submit_pr_review(self, pr_number: int, event: str = "APPROVE", body: str = "") -> Dict[str, Any]:
        payload = {
            "pr_number": pr_number,
            "event": event,
            "body": body,
            "dry_run": self.dry_run,
        }
        self.submit_pr_review_calls.append(payload)
        return payload

    def merge_or_reopen(
        self,
        pr_number: Optional[int],
        issue_number: Optional[int],
        decision: str,
        reason: str,
        reopen_comment: str = "",
    ) -> Dict[str, Any]:
        payload = {
            "pr_number": pr_number,
            "issue_number": issue_number,
            "decision": decision,
            "reason": reason,
            "reopen_comment": reopen_comment,
            "dry_run": self.dry_run,
        }
        self.merge_or_reopen_calls.append(payload)
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


def test_apply_retry_branch_suffix_appends_once() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=Path(tmpdir))
        orchestrator = WorkflowOrchestrator(
            project_root=Path("/data/workspaces/github-pm-agent"),
            engine=engine,
            actions=actions,
            client=object(),
            config={},
        )

        assert orchestrator._apply_retry_branch_suffix("ai/issue-5-city-search-hook", "-retry-1") == (
            "ai/issue-5-city-search-hook-retry-1"
        )
        assert orchestrator._apply_retry_branch_suffix("ai/issue-5-city-search-hook-retry-1", "-retry-1") == (
            "ai/issue-5-city-search-hook-retry-1"
        )


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

        result = orchestrator.process(_discussion_event(node_id="D_tech_review_gate"))

        phases_called = [c["prompt_path"] for c in engine.run_raw_text_handler_calls]
        assert any("tech_proposal" in p for p in phases_called)
        assert any("tech_review" in p for p in phases_called)
        assert not any("issue_breakdown" in p for p in phases_called)

        assert actions.create_issue_calls == []
        gate_comments = [
            call for call in actions.comment_on_discussion_calls if "**Phase `tech_review` complete.**" in call["message"]
        ]
        assert len(gate_comments) == 1
        gate_comment = gate_comments[0]
        assert gate_comment["discussion_id"] == "D_tech_review_gate"
        assert gate_comment["number"] == 5
        assert "**Phase `tech_review` complete.**" in gate_comment["message"]
        assert "use FastAPI" in gate_comment["message"]
        gate = result.get("gate")
        assert gate is not None
        assert gate["gate_discussion_node_id"] == "D_tech_review_gate"
        assert gate["next_phase"] == "assumption_check"
        assert gate["gate_posted_at"]
        assert result.get("created_issues", []) == []
        assert result.get("issue_creation_error", "") == ""

        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.get_artifacts().get("final_design") == "use FastAPI"
        assert final_instance.get_gate_issue_number() is None
        assert final_instance.get_discussion_gate_node_id() == "D_tech_review_gate"
        assert final_instance.get_gate_posted_at() == gate["gate_posted_at"]
        assert final_instance.get_gate_next_phase() == "assumption_check"
        assert final_instance.get_pending_comments() == []
        assert final_instance.is_completed() is False


def test_phase_workflow_output_per_role_uses_first_matching_agent_toolkit_by_role() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        default_actions = RecordingActions()
        first_engineer_actions = RecordingActions()
        second_engineer_actions = RecordingActions()
        engine = FakeEngine(default_actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(
            _project_root(),
            engine,
            default_actions,
            FakeClient({}),
            {
                "agents": [
                    {"id": "engineer-1", "role": "engineer"},
                    {"id": "engineer-2", "role": "engineer"},
                ]
            },
            agent_toolkits={
                "engineer-1": first_engineer_actions,
                "engineer-2": second_engineer_actions,
            },
        )

        result = orchestrator._process_phase_workflow(
            _discussion_event(node_id="D_role_output"),
            {
                "event_type": "discussion",
                "steps": [
                    {
                        "phase": "brainstorm_perspectives",
                        "roles": ["engineer"],
                        "prompt_path": "prompts/discussion/brainstorm_perspectives.md",
                        "output_per_role": True,
                        "gate": False,
                    }
                ],
            },
        )

        assert len(first_engineer_actions.comment_on_discussion_calls) == 1
        assert len(second_engineer_actions.comment_on_discussion_calls) == 0
        assert len(default_actions.comment_on_discussion_calls) == 1
        assert first_engineer_actions.comment_on_discussion_calls[0]["discussion_id"] == "D_role_output"
        assert first_engineer_actions.comment_on_discussion_calls[0]["message"].startswith("**[engineer]**")
        assert default_actions.comment_on_discussion_calls[0]["message"] == "Workflow complete."
        assert result["phase"] == "brainstorm_perspectives"


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


def test_create_issues_from_artifact_adds_ready_to_code_label() -> None:
    actions = RecordingActions()
    engine = FakeEngine(actions)
    orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

    created, error = orchestrator._create_issues_from_artifact(
        json.dumps(
            [
                {
                    "title": "Create app shell",
                    "body": "body",
                    "labels": ["frontend", "enhancement"],
                }
            ]
        ),
        _discussion_event(),
    )

    assert error == ""
    assert len(created) == 1
    assert actions.create_issue_calls[0]["labels"] == ["frontend", "enhancement", "ready-to-code"]


def test_issue_breakdown_does_not_create_duplicate_issues_when_refs_exist() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("issue_breakdown")
        instance.set_artifact(
            "issue_breakdown",
            json.dumps(
                [
                    {
                        "title": "Create app shell",
                        "body": "body",
                        "labels": ["frontend", "enhancement"],
                    }
                ]
            ),
        )
        instance.set_created_issue_refs([{"number": 101, "title": "Create app shell"}])

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event(node_id="D_disc_5"))

        assert result["created_issues"] == [
            {"title": "Create app shell", "number": 101, "result": {"number": 101}}
        ]
        assert actions.create_issue_calls == []
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.is_completed() is True


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


def test_record_discussion_comment_skips_owner_gate_reply() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("problem_synthesis")
        instance.set_discussion_gate("node-discussion-1", "2026-03-20T00:00:00Z", "brainstorm_perspectives")

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(
            _project_root(),
            engine,
            actions,
            FakeClient({}),
            {"github": {"owner": "alice"}},
        )

        result = orchestrator.process(_discussion_comment_event(actor="alice", body="approve"))

        assert result["recorded"] is False
        assert result["reason"] == "handled_by_gate_scanner"

        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert reloaded.get_pending_comments() == []


def test_record_discussion_comment_skips_owner_clarification_reply() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("problem_framing")
        instance.set_clarification("problem_framing", "2026-03-20T00:00:00Z")

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(
            _project_root(),
            engine,
            actions,
            FakeClient({}),
            {"github": {"owner": "alice"}},
        )

        result = orchestrator.process(_discussion_comment_event(actor="alice", body="Here is the answer."))

        assert result["recorded"] is False
        assert result["reason"] == "handled_by_gate_scanner"

        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert reloaded.get_pending_comments() == []


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

        result = orchestrator.process(_discussion_event(node_id="D_issue_breakdown_gate"))

        assert not result.get("terminated")
        assert actions.create_issue_calls == []
        gate_comments = [
            call for call in actions.comment_on_discussion_calls if "**Phase `tech_review` complete.**" in call["message"]
        ]
        assert len(gate_comments) == 1
        gate_comment = gate_comments[0]
        assert gate_comment["discussion_id"] == "D_issue_breakdown_gate"
        assert gate_comment["number"] == 5
        gate = result.get("gate")
        assert gate is not None
        assert gate["gate_discussion_node_id"] == "D_issue_breakdown_gate"
        assert gate["next_phase"] == "assumption_check"
        assert gate["gate_posted_at"]
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.get_artifacts().get("final_design") == "use FastAPI"
        assert final_instance.get_gate_issue_number() is None
        assert final_instance.get_discussion_gate_node_id() == "D_issue_breakdown_gate"
        assert final_instance.get_gate_posted_at() == gate["gate_posted_at"]
        assert final_instance.is_completed() is False


def test_evaluate_design_escalation_sets_issue_gate_posted_at() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("tech_review")
        instance.set_artifact("requirements", "some requirements text")
        instance.set_artifact("tech_proposal_engineer", "some proposal text")

        escalate_json = (
            '{"decision": "escalate", "docker_compatible": true, '
            '"evaluation_summary": "Need human input before issue breakdown.", "problem_coverage": []}'
        )

        class FakeEngineEscalate(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                self.run_raw_text_handler_calls.append({"event_id": event.event_id, "prompt_path": prompt_path, "role": role})
                if "tech_review" in prompt_path:
                    return {"raw_text": escalate_json}
                return {"raw_text": f"output for {role}"}

        actions = NumberedRecordingActions()
        engine = FakeEngineEscalate(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event())

        gate = result["gate"]
        assert gate["gate_issue_number"] == 100
        assert gate["next_phase"] == "tech_review"
        assert gate["gate_posted_at"]
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert final_instance.get_gate_issue_number() == 100
        assert final_instance.get_gate_posted_at() == gate["gate_posted_at"]


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


def test_discussion_clarification_limit_opens_gate_instead_of_terminating() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("problem_framing")
        instance.increment_clarification_round("problem_framing")
        instance.increment_clarification_round("problem_framing")

        class FakeEngineClarification(FakeEngine):
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
                return {"raw_text": 'blocking_unknowns: ["Which weather provider should we use?"]'}

        actions = RecordingActions()
        engine = FakeEngineClarification(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event(node_id="D_disc_5"))

        assert result["terminated"] is False
        assert result["gate"]["gate_discussion_node_id"] == "D_disc_5"
        assert result["gate"]["next_phase"] == "problem_synthesis"
        assert any(
            "automatic clarification limit" in call["message"]
            for call in actions.comment_on_discussion_calls
        )
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert reloaded.is_terminated() is False
        assert reloaded.is_awaiting_clarification() is False
        assert reloaded.get_discussion_gate_node_id() == "D_disc_5"


def test_issue_coding_clarification_limit_still_terminates_phase() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.reset_for_workflow_type("issue_coding")
        instance.set_phase("implement")
        instance.increment_clarification_round("implement")
        instance.increment_clarification_round("implement")

        class FakeEngineClarification(FakeEngine):
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
                return {"raw_text": 'blocking_unknowns: ["Which API should the implementation call?"]'}

        actions = RecordingActions()
        engine = FakeEngineClarification(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})
        orchestrator._prepare_phase_ai_cwd = lambda *args, **kwargs: None  # type: ignore[method-assign]

        result = orchestrator.process(_issue_coding_event())

        assert result["terminated"] is True
        assert "clarification limit" in result["terminated_reason"]
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.is_terminated() is True


def test_phase_workflow_gate_limit_terminates_phase() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("problem_synthesis")
        for _ in range(WorkflowOrchestrator.MAX_PHASE_GATE_OPENS):
            instance.increment_gate_open_count("problem_synthesis")

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_discussion_event(node_id="D_disc_5"))

        assert result["terminated"] is True
        assert "automatic gate limit" in result["terminated_reason"]
        assert len(actions.comment_on_discussion_calls) == 1
        assert "repeated human-confirmation loops" in actions.comment_on_discussion_calls[0]["message"]
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        assert reloaded.is_terminated() is True
        assert reloaded.get_discussion_gate_node_id() is None


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
                "response_type": "unclear",
            }
        ]
        resumed = store.pop()
        assert resumed is not None
        assert resumed.event_id.startswith("resume:")
        assert resumed.metadata["advance_to_phase"] == "issue_breakdown"
        assert resumed.metadata["gate_human_comment"] == ""


def test_phase_gate_scanner_clarification_resume_uses_new_event_id() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        instance.set_phase("problem_framing")
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
        instance.set_clarification("problem_framing", "2026-03-20T12:00:00Z", "D_disc_5")
        instance.set_artifact("problem_framing", "artifact")

        class FakeClientComments:
            def get_discussion_comments(self, owner: str, name: str, number: int) -> Any:
                return [
                    {
                        "createdAt": "2026-03-20T12:05:00Z",
                        "author": {"login": "owner"},
                        "body": "Here are the answers",
                    }
                ]

        scanner = PhaseGateScanner(store, FakeClientComments(), "owner")

        advanced = scanner.scan_and_advance()

        assert advanced == [
            {
                "repo": "songsjun/example",
                "discussion_number": 5,
                "from_phase": "problem_framing",
                "to_phase": "problem_framing",
                "response_type": "clarification_resume",
            }
        ]
        resumed = store.pop()
        assert resumed is not None
        assert resumed.event_id.startswith("resume:")
        assert resumed.metadata["advance_to_phase"] == "problem_framing"
        assert resumed.metadata["gate_human_comment"] == "Here are the answers"


def _issue_event(action: str = "opened", title: str = "Implement login page", body: str = "Users need to log in with email and password.", **metadata: Any) -> Event:
    return Event(
        event_id="evt-issue-1",
        event_type="issue_changed",
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/issues/42",
        title=title,
        body=body,
        target_kind="issue",
        target_number=42,
        metadata={"action": action, **metadata},
    )


def _issue_coding_event(title: str = "Implement login page", body: str = "Users need to log in with email and password.", **metadata: Any) -> Event:
    return Event(
        event_id="evt-issue-coding-1",
        event_type="issue_coding",
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/issues/42",
        title=title,
        body=body,
        target_kind="issue",
        target_number=42,
        metadata=dict(metadata),
    )


def _valid_coding_plan_json() -> str:
    return json.dumps(
        {
            "files": [{"path": "README.md", "content": "# test\n"}],
            "test_command": "npm test",
            "install_command": "npm install",
            "branch_name": "ai/issue-42",
            "commit_message": "feat: implement issue 42",
        }
    )


def test_issue_coding_pm_decision_opens_issue_gate_before_merge() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("pr_url", "https://example.test/pr/17")
        instance.set_artifact("code_review_combined", "LGTM — no issues found.")
        instance.set_artifact("test_result", json.dumps({"passed": True, "summary": "3 passed"}))

        class FakeEnginePMDecision(FakeEngine):
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
                return {
                    "raw_text": (
                        "```json\n"
                        '{"decision":"reopen","reason":"model picked the wrong outcome","reopen_comment":"ignore me"}\n'
                        "```\n"
                        "Tests passed and the review is clean."
                    )
                }

        actions = RecordingActions()
        engine = FakeEnginePMDecision(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_issue_coding_event())

        assert actions.merge_or_reopen_calls == []
        gate_comments = [c for c in actions.comment_calls if c.get("target_kind") == "issue"]
        assert len(gate_comments) == 1
        assert "confirm and execute this decision" in gate_comments[0]["message"]
        assert "Tests passed and the review is clean." in gate_comments[0]["message"]
        assert result["gate"]["gate_issue_number"] == 42
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.get_gate_issue_number() == 42
        assert final_instance.get_gate_next_phase() == "pm_decision"
        assert final_instance.get_gate_resume_mode() == "execute_action"
        assert final_instance.is_completed() is False


def test_issue_coding_pm_decision_confirmation_executes_without_rerunning_ai() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_artifact(
            "pm_decision",
            "```json\n"
            '{"decision":"reopen","reason":"model picked the wrong outcome","reopen_comment":"ignore me"}\n'
            "```\n"
            "Tests passed and the review is clean.",
        )
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("pr_url", "https://example.test/pr/17")
        instance.set_artifact("code_review_combined", "LGTM — no issues found.")
        instance.set_artifact("test_result", json.dumps({"passed": True, "summary": "3 passed"}))

        class FailingEngine(FakeEngine):
            def run_raw_text_handler(
                self,
                event: Any,
                prompt_path: str,
                role: str = "pm",
                variables: Optional[Dict[str, Any]] = None,
            ) -> Dict[str, Any]:
                raise AssertionError("pm_decision should not rerun after confirmation")

        actions = RecordingActions()
        engine = FailingEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(
            _issue_coding_event(
                advance_to_phase="pm_decision",
                execute_gated_action=True,
                gate_human_comment="ok",
            )
        )

        assert len(actions.merge_or_reopen_calls) == 1
        assert actions.merge_or_reopen_calls[0]["decision"] == "merge"
        assert actions.merge_or_reopen_calls[0]["reopen_comment"] == ""
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_completed() is True


def test_issue_coding_pm_decision_merge_conflict_requeues_merge_conflict_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_artifact(
            "pm_decision",
            "```json\n"
            '{"decision":"merge","reason":"tests and review passed"}\n'
            "```\n"
            "Merge is approved.",
        )
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("pr_url", "https://example.test/pr/17")
        instance.set_artifact("code_review_combined", "LGTM — no issues found.")
        instance.set_artifact("test_result", json.dumps({"passed": True, "summary": "3 passed"}))

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "mergeable": False,
                    "mergeable_state": "dirty",
                }
            }
        )
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, client, {})

        orchestrator.process(
            _issue_coding_event(
                advance_to_phase="pm_decision",
                execute_gated_action=True,
                gate_human_comment="ok",
            )
        )

        assert actions.merge_or_reopen_calls == []
        assert any(
            "no longer merges cleanly" in call["message"]
            for call in actions.comment_calls
            if call.get("target_kind") == "issue"
        )
        pending = [json.loads(line) for line in (runtime_dir / "queue_pending.jsonl").read_text().splitlines()]
        assert len(pending) == 1
        resumed = pending[0]
        assert resumed["metadata"]["advance_to_phase"] == "merge_conflict_resolution"
        assert resumed["metadata"]["gate_response_type"] == "merge_conflict"
        assert "no longer merges cleanly" in resumed["metadata"]["gate_human_comment"]
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_completed() is False
        assert final_instance.is_terminated() is False


def test_issue_coding_pm_decision_non_conflict_merge_failure_terminates() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_artifact(
            "pm_decision",
            "```json\n"
            '{"decision":"merge","reason":"tests and review passed"}\n'
            "```\n"
            "Merge is approved.",
        )
        instance.set_artifact("pr_number", "17")
        instance.set_artifact("pr_url", "https://example.test/pr/17")
        instance.set_artifact("code_review_combined", "LGTM — no issues found.")
        instance.set_artifact("test_result", json.dumps({"passed": True, "summary": "3 passed"}))

        class FailingMergeActions(RecordingActions):
            def merge_or_reopen(
                self,
                pr_number: Optional[int],
                issue_number: Optional[int],
                decision: str,
                reason: str,
                reopen_comment: str = "",
            ) -> Dict[str, Any]:
                raise subprocess.CalledProcessError(1, ["gh", "api"])

        actions = FailingMergeActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "mergeable": True,
                    "mergeable_state": "clean",
                }
            }
        )
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, client, {})

        orchestrator.process(
            _issue_coding_event(
                advance_to_phase="pm_decision",
                execute_gated_action=True,
                gate_human_comment="ok",
            )
        )

        assert any(
            "Final `merge` action failed" in call["message"]
            for call in actions.comment_calls
            if call.get("target_kind") == "issue"
        )
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is True
        assert (runtime_dir / "queue_pending.jsonl").exists() is False


def test_issue_coding_unparseable_review_output_terminates_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("code_review")
        instance.set_artifact("code_review", "Looks good to me overall.")
        instance.set_artifact("code_review_combined", "Looks good to me overall.")
        instance.set_artifact("pr_number", "17")

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(_issue_coding_event())

        assert actions.submit_pr_review_calls == []
        assert any(
            "could not be machine-verified" in call["message"]
            for call in actions.comment_calls
            if call.get("target_kind") == "issue"
        )
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is True


def test_issue_coding_combined_lgtm_reviews_are_accepted() -> None:
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("code_review")
        instance.set_artifact(
            "code_review_combined",
            "\n\n---\n\n".join(
                [
                    "### worker1_slot1\n\nLGTM — no issues found.",
                    "### worker2_slot2\n\nLGTM — no issues found.",
                ]
            ),
        )
        instance.set_artifact("code_review", "LGTM — no issues found.")
        instance.set_artifact("pr_number", "17")
        instance.set_original_event(_issue_coding_event().to_dict())

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(_issue_coding_event())

        assert len(actions.submit_pr_review_calls) == 1
        assert actions.submit_pr_review_calls[0]["event"] == "APPROVE"
        resumed = store.pop()
        assert resumed is not None
        assert resumed.event_id.startswith("resume:")
        assert resumed.metadata["advance_to_phase"] == "pm_decision"
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is False


def test_issue_coding_clean_review_with_conflicting_pr_requeues_merge_conflict_resolution() -> None:
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("code_review")
        instance.set_artifact(
            "code_review_combined",
            "\n\n---\n\n".join(
                [
                    "### worker1_slot1\n\nLGTM — no issues found.",
                    "### worker2_slot2\n\nLGTM — no issues found.",
                ]
            ),
        )
        instance.set_artifact("code_review", "LGTM — no issues found.")
        instance.set_artifact("pr_number", "17")
        instance.set_original_event(_issue_coding_event().to_dict())

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        client = FakeClient(
            {
                "repos/songsjun/example/pulls/17": {
                    "mergeable": False,
                    "mergeable_state": "dirty",
                }
            }
        )
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, client, {})

        orchestrator.process(_issue_coding_event())

        assert actions.submit_pr_review_calls == []
        assert any(
            "Resolve the branch conflict before opening the final merge gate." in call["message"]
            for call in actions.comment_calls
        )
        resumed = store.pop()
        assert resumed is not None
        assert resumed.metadata["advance_to_phase"] == "merge_conflict_resolution"
        assert resumed.metadata["gate_response_type"] == "merge_conflict"


def test_issue_coding_context_prep_failure_terminates_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        with patch.object(
            WorkflowOrchestrator,
            "_prepare_phase_ai_cwd",
            side_effect=RuntimeError("git clone failed: repository not found"),
        ):
            result = orchestrator.process(_issue_coding_event())

        assert result["terminated"] is True
        assert "failed to prepare repository context" in actions.comment_calls[0]["message"]
        assert engine.run_raw_text_handler_calls == []
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is True


def test_test_labeled_issue_scope_violation_terminates_before_coding_session() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        class FakeEngineScopeViolation(FakeEngine):
            def run_raw_text_handler(
                self,
                event: Any,
                prompt_path: str,
                role: str = "pm",
                variables: Optional[Dict[str, Any]] = None,
            ) -> Dict[str, Any]:
                return {
                    "raw_text": json.dumps(
                        {
                            "files": [
                                {
                                    "path": "src/stores/shortlist.ts",
                                    "content": "export const x = 1;\n",
                                }
                            ],
                            "test_command": "npm test",
                            "install_command": "npm install",
                            "branch_name": "ai/issue-42-shortlist-tests",
                            "commit_message": "test: add shortlist reducer coverage for issue #42",
                        }
                    )
                }

        actions = RecordingActions()
        engine = FakeEngineScopeViolation(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        issue_body = (
            "## What to change\n"
            "Create `src/stores/shortlist.test.ts`.\n\n"
            "## Location\n"
            "- File: `src/stores/shortlist.test.ts`\n"
            "- Function: `describe('shortlistReducer')`\n"
        )

        with patch.object(WorkflowOrchestrator, "_prepare_phase_ai_cwd", return_value=Path(tmpdir)):
            result = orchestrator.process(
                _issue_coding_event(
                    title="Add unit tests for shortlist reducer",
                    body=issue_body,
                    labels=["test", "frontend", "ready-to-code"],
                )
            )

        assert result["terminated"] is True
        assert any("violated the issue scope" in call["message"] for call in actions.comment_calls)
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is True
        assert actions.coding_session_calls == []


def test_test_labeled_issue_allows_test_files_and_test_config() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        class FakeEngineTestScoped(FakeEngine):
            def run_raw_text_handler(
                self,
                event: Any,
                prompt_path: str,
                role: str = "pm",
                variables: Optional[Dict[str, Any]] = None,
            ) -> Dict[str, Any]:
                if "code_review" in prompt_path:
                    return {"raw_text": "LGTM — no issues found."}
                return {
                    "raw_text": json.dumps(
                        {
                            "files": [
                                {
                                    "path": "src/stores/shortlist.test.ts",
                                    "content": "describe('shortlistReducer', () => {});\n",
                                },
                                {
                                    "path": "vitest.config.ts",
                                    "content": "export default {};\n",
                                },
                            ],
                            "test_command": "npm test",
                            "install_command": "npm install",
                            "branch_name": "ai/issue-42-shortlist-tests",
                            "commit_message": "test: add shortlist reducer coverage for issue #42",
                        }
                    )
                }

        actions = RecordingActions()
        engine = FakeEngineTestScoped(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        issue_body = (
            "## What to change\n"
            "Create `src/stores/shortlist.test.ts`.\n\n"
            "## Location\n"
            "- File: `src/stores/shortlist.test.ts`\n"
            "- Function: `describe('shortlistReducer')`\n"
        )

        with (
            patch.object(WorkflowOrchestrator, "_prepare_phase_ai_cwd", return_value=Path(tmpdir)),
            patch("github_pm_agent.coding_session.CodingSession.setup", return_value=None),
            patch("github_pm_agent.coding_session.CodingSession.apply_plan", return_value=None),
            patch(
                "github_pm_agent.coding_session.CodingSession.run_tests",
                return_value=CodingTestResult(
                    passed=True,
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                    summary="Tests PASSED (exit code 0).",
                ),
            ),
            patch("github_pm_agent.coding_session.CodingSession.push_branch", return_value="ai/issue-42-shortlist-tests"),
            patch(
                "github_pm_agent.coding_session.CodingSession.create_pr",
                return_value={"number": 17, "url": "https://example.test/pr/17"},
            ),
            patch("github_pm_agent.coding_session.CodingSession.cleanup", return_value=None),
        ):
            result = orchestrator.process(
                _issue_coding_event(
                    title="Add unit tests for shortlist reducer",
                    body=issue_body,
                    labels=["test", "frontend", "ready-to-code"],
                )
            )

        assert result["terminated"] is False
        assert actions.coding_session_calls
        assert not any("violated the issue scope" in call["message"] for call in actions.comment_calls)


def test_prepare_phase_ai_cwd_clones_with_auth_env_without_token_in_url() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(
            _project_root(),
            engine,
            actions,
            FakeClient({}),
            {"github": {"token": "secret-token"}},
        )

        recorded: list[dict[str, Any]] = []

        def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            recorded.append({"command": command, "kwargs": kwargs})
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("github_pm_agent.workflow_orchestrator.subprocess.run", side_effect=fake_run):
            ai_cwd = orchestrator._prepare_phase_ai_cwd(
                _issue_coding_event(),
                {"action": "coding_session"},
                [],
                {"branch_name": "ai/issue-2-place-search-parser"},
            )

        assert ai_cwd is not None
        clone_call = recorded[0]
        assert clone_call["command"] == [
            "git",
            "clone",
            "https://github.com/songsjun/example.git",
            str(ai_cwd),
        ]
        assert "secret-token" not in " ".join(clone_call["command"])
        env = clone_call["kwargs"]["env"]
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert "AUTHORIZATION: basic " in env["GIT_CONFIG_VALUE_0"]
        fetch_call = recorded[1]
        assert fetch_call["command"] == [
            "git",
            "fetch",
            "origin",
            "ai/issue-2-place-search-parser:refs/remotes/origin/ai/issue-2-place-search-parser",
        ]


def test_merge_conflict_resolution_prompt_receives_deterministic_conflict_context() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("merge_conflict_resolution")
        instance.set_artifact("branch_name", "ai/issue-42")
        instance.set_artifact("pr_number", "17")
        instance.set_original_event(_issue_coding_event(advance_to_phase="merge_conflict_resolution").to_dict())

        captured: dict[str, Any] = {}

        class FakeEngineMergeConflict(FakeEngine):
            def run_raw_text_handler(
                self,
                event: Any,
                prompt_path: str,
                role: str = "pm",
                variables: Optional[Dict[str, Any]] = None,
                cwd: Optional[str] = None,
            ) -> Dict[str, Any]:
                captured["prompt_path"] = prompt_path
                captured["variables"] = dict(variables or {})
                captured["cwd"] = cwd
                return {"raw_text": _valid_coding_plan_json()}

        actions = RecordingActions()
        engine = FakeEngineMergeConflict(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        with tempfile.TemporaryDirectory() as context_tmpdir:
            context_dir = Path(context_tmpdir)
            with (
                patch.object(WorkflowOrchestrator, "_prepare_phase_ai_cwd", return_value=context_dir),
                patch.object(
                    WorkflowOrchestrator,
                    "_collect_merge_conflict_prompt_context",
                    return_value={
                        "merge_conflict_details": "Conflicted files:\n- `jest.config.cjs`",
                        "merge_conflict_files": "- `jest.config.cjs`",
                    },
                ),
                patch("github_pm_agent.coding_session.CodingSession.setup", return_value=None),
                patch("github_pm_agent.coding_session.CodingSession.resolve_merge_conflict", return_value=None),
                patch(
                    "github_pm_agent.coding_session.CodingSession.run_tests",
                    return_value=CodingTestResult(
                        passed=True,
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                        summary="Tests PASSED (exit code 0).",
                    ),
                ),
                patch("github_pm_agent.coding_session.CodingSession.push_existing_branch", return_value=None),
                patch("github_pm_agent.coding_session.CodingSession.cleanup", return_value=None),
            ):
                orchestrator.process(_issue_coding_event(advance_to_phase="merge_conflict_resolution"))

        assert captured["prompt_path"] == "prompts/coding/merge_conflict_resolution.md"
        assert captured["variables"]["merge_conflict_details"] == "Conflicted files:\n- `jest.config.cjs`"
        assert captured["variables"]["merge_conflict_files"] == "- `jest.config.cjs`"


def test_issue_coding_blocking_reviews_requeue_fix_iteration_with_new_event_id() -> None:
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("code_review")
        instance.set_artifact(
            "code_review_combined",
            "\n\n---\n\n".join(
                [
                    (
                        "### worker1_slot1\n\n"
                        "**Blocking**\n"
                        "- **Location:** `src/lib/places.ts` line 1\n"
                        "- **Issue:** Latitude values outside the valid range are accepted.\n"
                        "- **Severity:** blocking\n"
                        "- **Fix suggestion:** Reject latitude values outside `[-90, 90]`.\n"
                    ),
                    "### worker2_slot2\n\nLGTM — no issues found.",
                ]
            ),
        )
        instance.set_artifact(
            "code_review",
            "**Blocking**\n"
            "- **Location:** `src/lib/places.ts` line 1\n"
            "- **Issue:** Latitude values outside the valid range are accepted.\n"
            "- **Severity:** blocking\n"
            "- **Fix suggestion:** Reject latitude values outside `[-90, 90]`.\n",
        )
        instance.set_artifact("pr_number", "17")
        instance.set_original_event(_issue_coding_event().to_dict())

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(_issue_coding_event())

        resumed = store.pop()
        assert resumed is not None
        assert resumed.event_id.startswith("resume:")
        assert resumed.event_id != "evt-issue-coding-1"
        assert resumed.metadata["advance_to_phase"] == "fix_iteration"
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.get_review_round() == 1
        assert final_instance.is_terminated() is False


def test_issue_coding_session_runtime_failure_terminates_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)

        class FakeEngineImplement(FakeEngine):
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
                return {"raw_text": _valid_coding_plan_json()}

        actions = RecordingActions()
        engine = FakeEngineImplement(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        with (
            patch.object(WorkflowOrchestrator, "_prepare_phase_ai_cwd", return_value=None),
            patch("github_pm_agent.coding_session.CodingSession.setup", side_effect=RuntimeError("boom")),
            patch("github_pm_agent.coding_session.CodingSession.cleanup", return_value=None),
        ):
            orchestrator.process(_issue_coding_event())

        assert any("Coding session failed: boom" in call["message"] for call in actions.comment_calls)
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is True
        assert "Coding session error" in final_instance.get_terminated_reason()


def test_issue_coding_max_iteration_failure_terminates_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_artifact("coding_iteration", "2")

        class FakeEngineImplement(FakeEngine):
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
                return {"raw_text": _valid_coding_plan_json()}

        actions = RecordingActions()
        engine = FakeEngineImplement(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        with (
            patch.object(WorkflowOrchestrator, "_prepare_phase_ai_cwd", return_value=None),
            patch("github_pm_agent.coding_session.CodingSession.setup", return_value=None),
            patch("github_pm_agent.coding_session.CodingSession.apply_plan", return_value=None),
            patch(
                "github_pm_agent.coding_session.CodingSession.run_tests",
                return_value=CodingTestResult(
                    passed=False,
                    exit_code=1,
                    stdout="",
                    stderr="",
                    summary="1 failed",
                ),
            ),
            patch("github_pm_agent.coding_session.CodingSession.cleanup", return_value=None),
        ):
            orchestrator.process(_issue_coding_event())

        assert any("Tests failed after 3 iteration" in call["message"] for call in actions.comment_calls)
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is True
        assert "Tests failed after 3 iteration" in final_instance.get_terminated_reason()


def test_issue_coding_retry_preserves_retry_branch_suffix_and_updates_plan_artifact() -> None:
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("implement")
        instance.set_original_event(_issue_coding_event().to_dict())

        class FakeEngineRetry(FakeEngine):
            def run_raw_text_handler(
                self,
                event: Any,
                prompt_path: str,
                role: str = "pm",
                variables: Optional[Dict[str, Any]] = None,
            ) -> Dict[str, Any]:
                return {
                    "raw_text": json.dumps(
                        {
                            "files": [{"path": "README.md", "content": "# test\n"}],
                            "test_command": "npm test",
                            "install_command": "npm install",
                            "branch_name": "ai/issue-42",
                            "commit_message": "feat: implement issue 42",
                        }
                    )
                }

        actions = RecordingActions()
        engine = FakeEngineRetry(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})
        resume_event = _issue_coding_event(
            advance_to_phase="implement",
            retry_branch_suffix="-retry-1",
            _queue={"attempt": 3},
        )

        with (
            patch.object(WorkflowOrchestrator, "_prepare_phase_ai_cwd", return_value=None),
            patch("github_pm_agent.coding_session.CodingSession.setup", return_value=None),
            patch("github_pm_agent.coding_session.CodingSession.apply_plan", return_value=None),
            patch(
                "github_pm_agent.coding_session.CodingSession.run_tests",
                return_value=CodingTestResult(
                    passed=False,
                    exit_code=1,
                    stdout="nope",
                    stderr="",
                    summary="1 failed",
                ),
            ),
            patch("github_pm_agent.coding_session.CodingSession.cleanup", return_value=None),
        ):
            orchestrator.process(resume_event)

        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].metadata["retry_branch_suffix"] == "-retry-1"
        assert pending[0].metadata["_queue"]["attempt"] == 4

        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        implement_artifact = str(reloaded.get_artifacts().get("implement") or "")
        assert '"branch_name": "ai/issue-42-retry-1"' in implement_artifact


def test_phase_gate_scanner_issue_gate_ignores_stale_comments_and_sets_execute_flag() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_gate(
            42,
            "pm_decision",
            posted_at="2026-03-20T12:00:00Z",
            resume_mode="execute_action",
        )
        instance.set_original_event(
            {
                "event_id": "evt-issue-coding-1",
                "event_type": "issue_coding",
                "source": "test",
                "occurred_at": "2026-03-20T00:00:00Z",
                "repo": "songsjun/example",
                "actor": "alice",
                "url": "https://example.test/issues/42",
                "title": "Implement login page",
                "body": "Users need to log in with email and password.",
                "target_kind": "issue",
                "target_number": 42,
                "metadata": {},
            }
        )

        class FakeClientComments:
            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if path == "repos/songsjun/example/issues/42/comments":
                    return [
                        {
                            "created_at": "2026-03-20T11:59:00Z",
                            "user": {"login": "owner"},
                            "body": "old comment",
                        },
                        {
                            "created_at": "2026-03-20T12:05:00Z",
                            "user": {"login": "owner"},
                            "body": "ok",
                        },
                    ]
                if path == "repos/songsjun/example/issues/42":
                    return {"state": "open"}
                return []

        scanner = PhaseGateScanner(store, FakeClientComments(), "owner")

        advanced = scanner.scan_and_advance()

        assert advanced == [
            {
                "repo": "songsjun/example",
                "discussion_number": 42,
                "from_phase": "pm_decision",
                "to_phase": "pm_decision",
                "response_type": "confirm",
            }
        ]
        resumed = store.pop()
        assert resumed is not None
        assert resumed.event_id.startswith("resume:")
        assert resumed.metadata["advance_to_phase"] == "pm_decision"
        assert resumed.metadata["execute_gated_action"] is True
        assert resumed.metadata["gate_human_comment"] == "ok"


def test_phase_gate_scanner_execute_action_unclear_comment_keeps_gate_open() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_gate(
            42,
            "pm_decision",
            posted_at="2026-03-20T12:00:00Z",
            resume_mode="execute_action",
        )
        instance.set_original_event(_issue_coding_event().to_dict())

        class FakeClientUnclear:
            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if path == "repos/songsjun/example/issues/42/comments":
                    return [
                        {
                            "created_at": "2026-03-20T12:05:00Z",
                            "user": {"login": "owner"},
                            "body": "thanks, looking",
                        }
                    ]
                if path == "repos/songsjun/example/issues/42":
                    return {"state": "open"}
                return []

        scanner = PhaseGateScanner(store, FakeClientUnclear(), "owner")

        advanced = scanner.scan_and_advance()

        assert advanced == []
        assert store.pop() is None
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.get_gate_issue_number() == 42
        assert reloaded.get_gate_resume_mode() == "execute_action"


def test_phase_gate_scanner_execute_action_advances_after_later_confirm() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_gate(
            42,
            "pm_decision",
            posted_at="2026-03-20T12:00:00Z",
            resume_mode="execute_action",
        )
        instance.set_original_event(_issue_coding_event().to_dict())

        class FakeClientMutable:
            def __init__(self) -> None:
                self.comments = [
                    {
                        "created_at": "2026-03-20T12:05:00Z",
                        "user": {"login": "owner"},
                        "body": "thanks, looking",
                    }
                ]

            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if path == "repos/songsjun/example/issues/42/comments":
                    return list(self.comments)
                if path == "repos/songsjun/example/issues/42":
                    return {"state": "open"}
                return []

        client = FakeClientMutable()
        scanner = PhaseGateScanner(store, client, "owner")

        assert scanner.scan_and_advance() == []
        client.comments.append(
            {
                "created_at": "2026-03-20T12:06:00Z",
                "user": {"login": "owner"},
                "body": "ok",
            }
        )

        advanced = scanner.scan_and_advance()

        assert advanced == [
            {
                "repo": "songsjun/example",
                "discussion_number": 42,
                "from_phase": "pm_decision",
                "to_phase": "pm_decision",
                "response_type": "confirm",
            }
        ]
        resumed = store.pop()
        assert resumed is not None
        assert resumed.metadata["execute_gated_action"] is True
        assert resumed.metadata["gate_human_comment"] == "ok"


def test_phase_gate_scanner_execute_action_unclear_limit_terminates_workflow() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_gate(
            42,
            "pm_decision",
            posted_at="2026-03-20T12:00:00Z",
            resume_mode="execute_action",
        )
        instance.set_original_event(_issue_coding_event().to_dict())

        class FakeClientMutable:
            def __init__(self) -> None:
                self.comments = [
                    {
                        "created_at": "2026-03-20T12:05:00Z",
                        "user": {"login": "owner"},
                        "body": "thanks, looking",
                    }
                ]

            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if path == "repos/songsjun/example/issues/42/comments":
                    return list(self.comments)
                if path == "repos/songsjun/example/issues/42":
                    return {"state": "open"}
                return []

        actions = RecordingActions()
        client = FakeClientMutable()
        scanner = PhaseGateScanner(store, client, "owner", actions)

        assert scanner.scan_and_advance() == []
        client.comments.append(
            {
                "created_at": "2026-03-20T12:06:00Z",
                "user": {"login": "owner"},
                "body": "still reviewing",
            }
        )

        advanced = scanner.scan_and_advance()

        assert advanced == [
            {
                "repo": "songsjun/example",
                "discussion_number": 42,
                "from_phase": "pm_decision",
                "to_phase": "pm_decision",
                "response_type": "unclear_limit",
            }
        ]
        assert store.pop() is None
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.is_terminated() is True
        assert reloaded.get_gate_issue_number() is None
        assert any(
            "received 2 unclear confirmation response" in call["message"]
            for call in actions.comment_calls
            if call.get("target_kind") == "issue"
        )


def test_phase_gate_scanner_execute_action_does_not_treat_closed_issue_as_confirmation() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_gate(
            42,
            "pm_decision",
            posted_at="2026-03-20T12:00:00Z",
            resume_mode="execute_action",
        )
        instance.set_original_event(_issue_coding_event().to_dict())

        class FakeClientClosedIssue:
            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if path == "repos/songsjun/example/issues/42/comments":
                    return []
                if path == "repos/songsjun/example/issues/42":
                    return {"state": "closed"}
                return []

        scanner = PhaseGateScanner(store, FakeClientClosedIssue(), "owner")

        advanced = scanner.scan_and_advance()

        assert advanced == []
        assert store.pop() is None
        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert reloaded.get_gate_issue_number() == 42


def test_phase_gate_scanner_repeated_issue_gate_uses_gate_instance_key() -> None:
    from github_pm_agent.phase_gate_scanner import PhaseGateScanner
    from github_pm_agent.queue_store import QueueStore
    from github_pm_agent.utils import append_jsonl

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        store = QueueStore(runtime_dir)

        append_jsonl(
            runtime_dir / "gate_advanced.jsonl",
            {
                "gate_key": "songsjun/example:issue:42:pm_decision:2026-03-20T12:00:00Z:execute_action",
                "repo": "songsjun/example",
                "discussion_number": 42,
                "from_phase": "pm_decision",
                "to_phase": "pm_decision",
                "response_type": "confirm",
                "advanced_at": "2026-03-20T12:01:00Z",
            },
        )

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("pm_decision")
        instance.set_gate(
            42,
            "pm_decision",
            posted_at="2026-03-20T13:00:00Z",
            resume_mode="execute_action",
        )
        instance.set_original_event(_issue_coding_event().to_dict())

        class FakeClientComments:
            def api(self, path: str, params: Any = None, method: str = "GET") -> Any:
                if path == "repos/songsjun/example/issues/42/comments":
                    return [
                        {
                            "created_at": "2026-03-20T13:05:00Z",
                            "user": {"login": "owner"},
                            "body": "ok",
                        }
                    ]
                if path == "repos/songsjun/example/issues/42":
                    return {"state": "open"}
                return []

        scanner = PhaseGateScanner(store, FakeClientComments(), "owner")

        advanced = scanner.scan_and_advance()

        assert advanced == [
            {
                "repo": "songsjun/example",
                "discussion_number": 42,
                "from_phase": "pm_decision",
                "to_phase": "pm_decision",
                "response_type": "confirm",
            }
        ]
        resumed = store.pop()
        assert resumed is not None
        assert resumed.metadata["execute_gated_action"] is True


def test_review_output_with_trailing_newlines_is_not_contract_violation() -> None:
    """Whitespace between/after blocks must not trigger contract_violation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("code_review")
        # Real AI output: two blocks separated by a blank line, trailing newline
        combined = (
            "**Warning** — unused import in utils.py\n\n"
            "- **Severity:** warning\n\n"
            "**Warning** — missing docstring on public method\n\n"
            "- **Severity:** warning\n"
        )
        # Both artifacts must be set so the idempotency guard fires and skips AI re-run.
        instance.set_artifact("code_review", combined)
        instance.set_artifact("code_review_combined", combined)
        instance.set_artifact("pr_number", "17")
        instance.set_original_event(_issue_coding_event().to_dict())

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(_issue_coding_event())

        # Warnings only → should approve PR, NOT terminate
        assert len(actions.submit_pr_review_calls) == 1
        assert actions.submit_pr_review_calls[0]["event"] == "APPROVE"
        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_terminated() is False


def test_issue_changed_workflow_runs_on_opened() -> None:
    """issue_changed workflow runs worker analysis when action=opened."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_issue_event(action="opened"))

        assert result.get("skipped") is not True
        # Worker analysis phase should have run
        phases = [c["prompt_path"] for c in engine.run_raw_text_handler_calls]
        assert any("issue/worker_analysis" in p for p in phases)
        assert result.get("phase") == "issue_analysis"


def test_issue_changed_workflow_skips_on_edited() -> None:
    """issue_changed workflow skips when action != opened (e.g. edited)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_issue_event(action="edited"))

        assert result.get("skipped") is True
        assert "trigger_action" in result.get("reason", "")
        assert engine.run_raw_text_handler_calls == []


def test_issue_changed_workflow_posts_comments_on_issue() -> None:
    """Workers post comments to the issue (not discussion) when output_per_role is true."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(_issue_event(action="opened"))

        # Should have posted worker output comments to the issue
        issue_comments = [c for c in actions.comment_calls if c.get("target_kind") == "issue"]
        assert len(issue_comments) >= 1
        # No discussion comments should have been posted
        discussion_comments = actions.comment_on_discussion_calls
        assert len(discussion_comments) == 0


def test_issue_changed_workflow_skips_when_already_completed() -> None:
    """Second trigger for the same issue is skipped (idempotent)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        instance.set_phase("issue_analysis")
        instance.set_completed()

        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        result = orchestrator.process(_issue_event(action="opened"))

        assert result.get("skipped") is True
        assert result.get("reason") == "workflow_completed"
        assert engine.run_raw_text_handler_calls == []


def test_issue_changed_workflow_completes_after_single_pass() -> None:
    """After running worker analysis, workflow is marked complete with no gate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator.process(_issue_event(action="opened"))

        final_instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 42)
        assert final_instance.is_completed() is True
        assert final_instance.get_gate_next_phase() is None


# ---------------------------------------------------------------------------
# classify_gate_response
# ---------------------------------------------------------------------------

from github_pm_agent.phase_gate_scanner import classify_gate_response


def test_classify_confirm_keywords() -> None:
    for text in ["ok", "确认", "LGTM", "yes proceed", "好的"]:
        assert classify_gate_response(text) == "confirm", f"expected confirm for {text!r}"


def test_classify_reject_keywords() -> None:
    for text in ["no", "不对", "reject", "redo", "start over", "nope"]:
        assert classify_gate_response(text) == "reject", f"expected reject for {text!r}"


def test_classify_confirm_revise_requires_explicit_confirm() -> None:
    """confirm_revise needs a confirm keyword plus a revise signal."""
    assert classify_gate_response("ok but also add dark mode") == "confirm_revise"
    assert classify_gate_response("确认，另外加上登录功能") == "confirm_revise"
    assert classify_gate_response("yes, but change the approach") == "confirm_revise"


def test_classify_long_reply_without_confirm_is_unclear() -> None:
    """Long replies with no confirm keyword must be 'unclear', not auto-confirm_revise."""
    long_rejection = "我觉得这个方向完全错了，需要从头重新考虑，整个设计都有问题，请重新来过"
    result = classify_gate_response(long_rejection)
    # Should NOT be confirm_revise — no explicit confirm keyword
    assert result in ("unclear", "reject"), f"got {result!r} for ambiguous long reply"

    vague_long = "I think we should probably reconsider the overall direction here completely"
    result2 = classify_gate_response(vague_long)
    assert result2 == "unclear", f"got {result2!r} for vague long reply with no confirm"


def test_classify_empty_is_unclear() -> None:
    assert classify_gate_response("") == "unclear"
    assert classify_gate_response("   ") == "unclear"


# ---------------------------------------------------------------------------
# combined artifact injection
# ---------------------------------------------------------------------------


def test_slot_phase_saves_combined_artifact() -> None:
    """After a slot-based output_per_role phase, {phase}_combined artifact is saved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()

        call_index = [0]

        class FakeEngineSlots(FakeEngine):
            def run_raw_text_handler(self, event: Any, prompt_path: str, role: str = "pm", variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                call_index[0] += 1
                return {"raw_text": f"output from slot {call_index[0]}"}

        engine = FakeEngineSlots(actions, runtime_dir=runtime_dir)
        agent_configs = [
            {"id": "w1", "role": "worker", "worker_index": 1},
            {"id": "w2", "role": "worker", "worker_index": 2},
        ]
        orchestrator = WorkflowOrchestrator(
            _project_root(), engine, actions, FakeClient({}), {}, agent_configs=agent_configs
        )

        orchestrator._process_phase_workflow(
            _discussion_event(node_id="D_combined"),
            {
                "event_type": "discussion",
                "steps": [
                    {
                        "phase": "problem_framing",
                        "slots": 2,
                        "prompt_path": "prompts/discussion/problem_framing.md",
                        "output_per_role": True,
                        "gate": False,
                    }
                ],
            },
        )

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        combined = instance.get_artifacts().get("problem_framing_combined")
        assert combined is not None, "combined artifact must be saved"
        assert "output from slot 1" in combined
        assert "output from slot 2" in combined
        assert "---" in combined  # separator between slots


def test_single_executor_phase_does_not_save_combined_artifact() -> None:
    """A single-role phase (no slots) should NOT save a _combined artifact."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        actions = RecordingActions()
        engine = FakeEngine(actions, runtime_dir=runtime_dir)
        orchestrator = WorkflowOrchestrator(_project_root(), engine, actions, FakeClient({}), {})

        orchestrator._process_phase_workflow(
            _discussion_event(node_id="D_single"),
            {
                "event_type": "discussion",
                "steps": [
                    {
                        "phase": "brainstorm",
                        "roles": ["pm"],
                        "prompt_path": "prompts/discussion/brainstorm.md",
                        "output_per_role": True,
                        "gate": False,
                    }
                ],
            },
        )

        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 5)
        artifacts = instance.get_artifacts()
        assert "brainstorm_combined" not in artifacts
