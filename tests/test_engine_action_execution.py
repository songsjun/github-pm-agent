import tempfile
import unittest
from pathlib import Path

from github_pm_agent.engine import EventEngine
from github_pm_agent.models import Event


class FakeAIManager:
    def default_provider(self) -> str:
        return "fake"

    def default_model(self, provider_name: str = "") -> str:
        return "fake-model"


class FakeActions:
    def __init__(self) -> None:
        self.calls = []

    def assign(self, target_kind, target_number, users):
        self.calls.append(("assign", target_kind, target_number, list(users)))
        return {"kind": target_kind, "number": target_number, "users": list(users)}

    def request_review(self, target_number, reviewers):
        self.calls.append(("review_request", target_number, list(reviewers)))
        return {"number": target_number, "reviewers": list(reviewers)}

    def set_state(self, target_kind, target_number, state):
        self.calls.append(("state", target_kind, target_number, state))
        return {"kind": target_kind, "number": target_number, "state": state}


class EventEngineActionExecutionTest(unittest.TestCase):
    def _event(self, target_kind="issue", target_number=17) -> Event:
        return Event(
            event_id="evt-1",
            event_type="issue_changed",
            source="issues",
            occurred_at="2026-03-19T12:00:00Z",
            repo="acme/widgets",
            actor="alice",
            url="https://example.test/issues/17",
            title="Example",
            body="",
            target_kind=target_kind,
            target_number=target_number,
            metadata={},
        )

    def test_assign_action_executes_with_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actions = FakeActions()
            engine = EventEngine({"engine": {}}, FakeAIManager(), actions, Path(tmp))
            event = self._event()

            plan = engine.make_plan(
                should_act=True,
                reason="assign owner",
                action_type="assign",
                target_kind="issue",
                target_number=17,
                message="",
                action_input={"users": ["alice"]},
            )
            result = engine.finish_plan(event, plan)

            self.assertEqual(actions.calls, [("assign", "issue", 17, ["alice"])])
            self.assertEqual(result["action"]["action_type"], "assign")

    def test_review_request_action_executes_on_pull_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actions = FakeActions()
            engine = EventEngine({"engine": {}}, FakeAIManager(), actions, Path(tmp))
            event = self._event(target_kind="pull_request", target_number=21)

            plan = engine.make_plan(
                should_act=True,
                reason="request review",
                action_type="review_request",
                target_kind="pull_request",
                target_number=21,
                message="",
                action_input={"users": ["reviewer1", "reviewer2"]},
            )
            result = engine.finish_plan(event, plan)

            self.assertEqual(actions.calls, [("review_request", 21, ["reviewer1", "reviewer2"])])
            self.assertEqual(result["action"]["action_type"], "review_request")

    def test_state_action_executes_with_requested_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actions = FakeActions()
            engine = EventEngine({"engine": {}}, FakeAIManager(), actions, Path(tmp))
            event = self._event(target_kind="pull_request", target_number=33)

            plan = engine.make_plan(
                should_act=True,
                reason="close stale pr",
                action_type="state",
                target_kind="pull_request",
                target_number=33,
                message="",
                action_input={"state": "closed"},
            )
            result = engine.finish_plan(event, plan)

            self.assertEqual(actions.calls, [("state", "pull_request", 33, "closed")])
            self.assertEqual(result["action"]["raw"]["state"], "closed")
