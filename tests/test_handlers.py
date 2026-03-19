import unittest

from github_pm_agent.handlers import (
    handle_workflow_failed,
    handle_issue_event_review_requested,
    handle_pull_request_review_approved,
    handle_pull_request_review_changes_requested,
    resolve_handler,
)
from github_pm_agent.models import Event


class FakeEngine:
    def __init__(self) -> None:
        self.calls = []

    def make_plan(self, **kwargs):
        self.calls.append(("make_plan", kwargs))
        return kwargs

    def finish_plan(self, event, plan):
        self.calls.append(("finish_plan", event.event_type, plan))
        return {
            "plan": plan,
            "action": {
                "executed": plan.get("should_act", False),
                "action_type": plan.get("action_type", "none"),
                "target": plan.get("target", {}),
                "message": plan.get("message", ""),
                "raw": {"dry_run": True},
            },
        }

    def run_ai_handler(self, event, prompt_path):
        self.calls.append(("run_ai_handler", event.event_type, prompt_path))
        return {
            "plan": {
                "should_act": False,
                "reason": "ai-route",
                "action_type": "none",
                "target": {"kind": event.target_kind, "number": event.target_number or 0},
                "message": "",
                "labels_to_add": [],
                "labels_to_remove": [],
                "memory_note": "",
                "issue_title": "",
            },
            "action": {
                "executed": False,
                "action_type": "none",
                "target": {"kind": event.target_kind, "number": event.target_number or 0},
                "message": "",
                "raw": {},
            },
        }


class HandlerResolutionTest(unittest.TestCase):
    def test_resolve_high_value_handlers(self) -> None:
        engine = FakeEngine()
        cases = [
            ("workflow_failed", "workflow_failed"),
            ("pull_request_review", "pull_request_review_changes_requested", {"state": "CHANGES_REQUESTED"}),
            ("pull_request_review", "pull_request_review_approved", {"state": "APPROVED"}),
            ("issue_event_review_requested", "issue_event_review_requested", {"review_requested_reviewer": "bob"}),
            ("discussion_comment", "discussion_ai"),
        ]
        for item in cases:
            if len(item) == 2:
                event_type, expected = item
                metadata = {}
            else:
                event_type, expected, metadata = item
            event = self._event(event_type, metadata=metadata)
            name, _ = resolve_handler(engine, event)
            self.assertEqual(name, expected)

    def test_pull_request_review_changes_requested_builds_comment(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "pull_request_review",
            actor="reviewer1",
            body="Please split this into smaller pieces. Add tests for the edge case.",
            metadata={"state": "CHANGES_REQUESTED"},
        )
        result = handle_pull_request_review_changes_requested(engine, event)
        self.assertTrue(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "comment")
        self.assertIn("@reviewer1", result["plan"]["message"])
        self.assertIn("split this", result["plan"]["message"])

    def test_pull_request_review_approved_is_memory_only(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "pull_request_review",
            actor="reviewer2",
            metadata={"state": "APPROVED"},
        )
        result = handle_pull_request_review_approved(engine, event)
        self.assertFalse(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "none")
        self.assertIn("approved", result["plan"]["memory_note"])

    def test_review_requested_prompts_pr_comment(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "issue_event_review_requested",
            actor="author1",
            metadata={"review_requested_reviewer": "alice"},
        )
        result = handle_issue_event_review_requested(engine, event)
        self.assertTrue(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "comment")
        self.assertIn("@alice", result["plan"]["message"])
        self.assertIn("@author1", result["plan"]["message"])

    def test_workflow_failed_routes_to_ai(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "workflow_failed",
            target_kind="workflow_run",
            target_number=88,
            body="workflow failed on test job",
        )
        result = handle_workflow_failed(engine, event)
        self.assertEqual(engine.calls[0][0], "run_ai_handler")
        self.assertEqual(result["plan"]["reason"], "ai-route")

    def test_discussion_comments_route_to_ai(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "discussion_comment",
            target_kind="discussion",
            target_number=4,
            body="We should decide on the release rule here.",
        )
        name, handler = resolve_handler(engine, event)
        self.assertEqual(name, "discussion_ai")
        result = handler(engine, event)
        self.assertEqual(result["plan"]["reason"], "ai-route")

    def _event(
        self,
        event_type: str,
        actor: str = "actor",
        body: str = "",
        metadata=None,
        target_kind: str = "pull_request",
        target_number: int = 17,
    ):
        return Event(
            event_id=f"evt-{event_type}",
            event_type=event_type,
            source="test",
            occurred_at="2026-03-19T00:00:00Z",
            repo="songsjun/example",
            actor=actor,
            url="https://example.test",
            title=f"title-{event_type}",
            body=body,
            target_kind=target_kind,
            target_number=target_number,
            metadata=metadata or {},
        )


if __name__ == "__main__":
    unittest.main()
