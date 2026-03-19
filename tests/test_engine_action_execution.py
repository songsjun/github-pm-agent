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

    def unassign(self, target_kind, target_number, users):
        self.calls.append(("unassign", target_kind, target_number, list(users)))
        return {"kind": target_kind, "number": target_number, "users": list(users)}

    def request_review(self, target_number, reviewers):
        self.calls.append(("review_request", target_number, list(reviewers)))
        return {"number": target_number, "reviewers": list(reviewers)}

    def remove_reviewers(self, target_number, reviewers):
        self.calls.append(("remove_reviewer", target_number, list(reviewers)))
        return {"number": target_number, "reviewers": list(reviewers)}

    def edit(self, target_kind, target_number, fields):
        self.calls.append(("edit", target_kind, target_number, dict(fields)))
        return {"kind": target_kind, "number": target_number, "fields": dict(fields)}

    def set_milestone(self, target_kind, target_number, milestone):
        self.calls.append(("milestone", target_kind, target_number, milestone))
        return {"kind": target_kind, "number": target_number, "milestone": milestone}

    def set_state(self, target_kind, target_number, state):
        self.calls.append(("state", target_kind, target_number, state))
        return {"kind": target_kind, "number": target_number, "state": state}

    def mark_pull_request_draft(self, target_number):
        self.calls.append(("draft", target_number))
        return {"number": target_number}

    def mark_pull_request_ready(self, target_number):
        self.calls.append(("ready_for_review", target_number))
        return {"number": target_number}

    def merge_pull_request(self, target_number, params):
        self.calls.append(("merge", target_number, dict(params)))
        return {"number": target_number, "params": dict(params)}

    def submit_review_decision(self, target_number, decision, body="", commit_id=""):
        self.calls.append(("review_decision", target_number, decision, body, commit_id))
        return {"number": target_number, "decision": decision}

    def rerun_workflow(self, run_id):
        self.calls.append(("rerun_workflow", run_id))
        return {"run_id": run_id}

    def cancel_workflow(self, run_id):
        self.calls.append(("cancel_workflow", run_id))
        return {"run_id": run_id}

    def create_release(self, **fields):
        self.calls.append(("release", dict(fields)))
        return {"fields": dict(fields)}

    def create_discussion(self, repository_id, category_id, title, body):
        self.calls.append(("create_discussion", repository_id, category_id, title, body))
        return {"id": "discussion"}

    def update_discussion(self, discussion_id, title="", body="", category_id=""):
        self.calls.append(("update_discussion", discussion_id, title, body, category_id))
        return {"id": discussion_id}

    def update_project_field(self, project_id, item_id, field_id, value):
        self.calls.append(("project", project_id, item_id, field_id, dict(value)))
        return {"project_id": project_id}


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

    def test_additional_action_types_dispatch_to_toolkit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actions = FakeActions()
            engine = EventEngine({"engine": {}}, FakeAIManager(), actions, Path(tmp))
            event = self._event(target_kind="pull_request", target_number=44)

            scenarios = [
                ("unassign", {"users": ["alice"]}, ("unassign", "pull_request", 44, ["alice"])),
                ("remove_reviewer", {"reviewers": ["bob"]}, ("remove_reviewer", 44, ["bob"])),
                ("edit", {"title": "New", "body": "Body"}, ("edit", "pull_request", 44, {"title": "New", "body": "Body"})),
                ("milestone", {"milestone": 12}, ("milestone", "pull_request", 44, 12)),
                ("draft", {}, ("draft", 44)),
                ("ready_for_review", {}, ("ready_for_review", 44)),
                ("merge", {"merge_method": "squash"}, ("merge", 44, {"merge_method": "squash"})),
                ("review_decision", {"decision": "approve", "body": "LGTM"}, ("review_decision", 44, "approve", "LGTM", "")),
                ("rerun_workflow", {"run_id": 99}, ("rerun_workflow", 99)),
                ("cancel_workflow", {"run_id": 100}, ("cancel_workflow", 100)),
                ("create_release", {"tag_name": "v1.0.0"}, ("release", {"tag_name": "v1.0.0"})),
                ("create_discussion", {"repository_id": "repo", "category_id": "cat", "title": "T", "body": "B"}, ("create_discussion", "repo", "cat", "T", "B")),
                ("update_discussion", {"discussion_id": "disc", "title": "T2"}, ("update_discussion", "disc", "T2", "", "")),
                ("project", {"project_id": "proj", "item_id": "item", "field_id": "field", "value": {"text": "done"}}, ("project", "proj", "item", "field", {"text": "done"})),
            ]

            for action_type, action_input, expected_call in scenarios:
                plan = engine.make_plan(
                    should_act=True,
                    reason=action_type,
                    action_type=action_type,
                    target_kind="pull_request" if action_type not in {"create_release", "project"} else "repo",
                    target_number=44 if action_type not in {"create_release", "project", "rerun_workflow", "cancel_workflow"} else (99 if action_type == "rerun_workflow" else 100 if action_type == "cancel_workflow" else None),
                    message="",
                    action_input=action_input,
                )
                actions.calls.clear()
                result = engine.finish_plan(event, plan)
                self.assertEqual(actions.calls[0], expected_call)
                self.assertEqual(result["action"]["action_type"], action_type)
