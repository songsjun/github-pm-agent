import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from github_pm_agent.handlers import (
    handle_issue_event_assigned,
    handle_issue_event_closed,
    handle_workflow_failed,
    handle_workflow_run,
    handle_issue_event_review_requested,
    handle_issue_event_reopened,
    handle_pull_request_review_approved,
    handle_pull_request_review_changes_requested,
    resolve_handler,
)
from github_pm_agent.models import Event
from github_pm_agent.capability_routing import route_for_event


class FakeEngine:
    def __init__(self) -> None:
        self.calls = []
        self.config = {"_project_root": str(Path("/Users/sjunsong/Workspace/github-pm-agent"))}

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

    def run_ai_handler(self, event, prompt_path, skill_refs=None):
        self.calls.append(("run_ai_handler", event.event_type, prompt_path, tuple(skill_refs or ())))
        return {
            "plan": {
                "should_act": False,
                "reason": "ai-route",
                "action_type": "none",
                "target": {"kind": event.target_kind, "number": event.target_number or 0},
                "message": "",
                "labels_to_add": [],
                "labels_to_remove": [],
                "action_input": {},
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
            ("workflow_run", "workflow_run_observation"),
            ("workflow_failed", "workflow_failed"),
            ("issue_event_closed", "issue_closed_observation"),
            ("issue_event_reopened", "issue_reopened_followup"),
            ("issue_event_assigned", "issue_assigned_observation", {"assignee": "alice"}),
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
        self.assertEqual(result["routing"]["stage"], "blocked_work")
        self.assertIn(
            result["routing"]["prompt_path"],
            {
                "prompts/actions/blocker_investigation.md",
                "prompts/actions/default_event.md",
            },
        )

    def test_workflow_run_is_memory_only(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "workflow_run",
            target_kind="workflow_run",
            target_number=88,
            metadata={"status": "completed", "conclusion": "success"},
        )
        result = handle_workflow_run(engine, event)
        self.assertFalse(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "none")
        self.assertIn("workflow run #88", result["plan"]["memory_note"])
        self.assertEqual(engine.calls[0][0], "make_plan")

    def test_issue_closed_is_memory_only(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "issue_event_closed",
            actor="closer1",
            target_kind="issue",
            target_number=23,
        )
        result = handle_issue_event_closed(engine, event)
        self.assertFalse(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "none")
        self.assertIn("@closer1", result["plan"]["memory_note"])

    def test_issue_reopened_requests_new_status_update(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "issue_event_reopened",
            actor="reopener1",
            target_kind="issue",
            target_number=24,
        )
        result = handle_issue_event_reopened(engine, event)
        self.assertTrue(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "comment")
        self.assertIn("@reopener1", result["plan"]["message"])
        self.assertIn("next concrete action", result["plan"]["message"])

    def test_issue_assigned_is_memory_only(self) -> None:
        engine = FakeEngine()
        event = self._event(
            "issue_event_assigned",
            actor="manager1",
            target_kind="issue",
            target_number=25,
            metadata={"assignee": "owner1"},
        )
        result = handle_issue_event_assigned(engine, event)
        self.assertFalse(result["plan"]["should_act"])
        self.assertEqual(result["plan"]["action_type"], "none")
        self.assertIn("@owner1", result["plan"]["memory_note"])
        self.assertIn("@manager1", result["plan"]["memory_note"])

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
        self.assertEqual(result["routing"]["stage"], "clarify")

    def test_route_for_event_prefers_stage_assets_when_present(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prompts_dir = root / "prompts" / "actions"
            skills_dir = root / "skills"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            skills_dir.mkdir(parents=True, exist_ok=True)
            (prompts_dir / "default_event.md").write_text("fallback", encoding="utf-8")
            (prompts_dir / "blocker_investigation.md").write_text("blocker prompt", encoding="utf-8")
            (skills_dir / "pm-core.md").write_text("core", encoding="utf-8")
            (skills_dir / "blocked-work.md").write_text("blocked skill", encoding="utf-8")

            route = route_for_event(root, self._event("workflow_failed", target_kind="workflow_run"))

            self.assertEqual(route.stage, "blocked_work")
            self.assertEqual(route.prompt_path, "prompts/actions/blocker_investigation.md")
            self.assertEqual(route.skill_refs[0], "skills/blocked-work.md")

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
