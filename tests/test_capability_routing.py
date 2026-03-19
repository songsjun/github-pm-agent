import unittest
from pathlib import Path

from github_pm_agent.capability_routing import route_for_event
from github_pm_agent.models import Event


PROJECT_ROOT = Path("/Users/sjunsong/Workspace/github-pm-agent")


def make_event(event_type: str, *, target_kind: str = "issue", target_number: int = 1) -> Event:
    return Event(
        event_id=f"evt-{event_type}",
        event_type=event_type,
        source="test",
        occurred_at="2026-03-19T00:00:00Z",
        repo="songsjun/example",
        actor="agent",
        url="https://example.test",
        title=f"title-{event_type}",
        body="body",
        target_kind=target_kind,
        target_number=target_number,
        metadata={},
    )


class CapabilityAssetInventoryTest(unittest.TestCase):
    def test_expected_stage_assets_exist(self) -> None:
        expected_paths = [
            "skills/pm-core.md",
            "skills/clarify.md",
            "skills/scope-guard.md",
            "skills/blocked-work.md",
            "skills/review-readiness.md",
            "skills/release-readiness.md",
            "prompts/actions/default_event.md",
            "prompts/actions/intake_clarify.md",
            "prompts/actions/spec_review.md",
            "prompts/actions/blocker_investigation.md",
            "prompts/actions/review_readiness.md",
            "prompts/actions/release_readiness.md",
            "prompts/actions/retro_summary.md",
        ]

        missing = [path for path in expected_paths if not (PROJECT_ROOT / path).exists()]
        self.assertEqual(missing, [])


class CapabilityRoutingTest(unittest.TestCase):
    def test_issue_events_use_clarify_route_assets(self) -> None:
        route = route_for_event(PROJECT_ROOT, make_event("issue_changed"))

        self.assertEqual(route.stage, "clarify")
        self.assertEqual(route.prompt_path, "prompts/actions/intake_clarify.md")
        self.assertIn("skills/clarify.md", route.skill_refs)
        self.assertIn("skills/pm-core.md", route.skill_refs)

    def test_pull_request_events_use_review_readiness_assets(self) -> None:
        route = route_for_event(PROJECT_ROOT, make_event("pull_request_changed", target_kind="pull_request"))

        self.assertEqual(route.stage, "review_readiness")
        self.assertEqual(route.prompt_path, "prompts/actions/review_readiness.md")
        self.assertIn("skills/review-readiness.md", route.skill_refs)
        self.assertIn("skills/scope-guard.md", route.skill_refs)
        self.assertIn("skills/pm-core.md", route.skill_refs)

    def test_commit_events_use_release_prompt_and_release_skill(self) -> None:
        route = route_for_event(PROJECT_ROOT, make_event("commit", target_kind="commit", target_number=0))

        self.assertEqual(route.stage, "release_readiness")
        self.assertEqual(route.prompt_path, "prompts/actions/release_readiness.md")
        self.assertEqual(route.skill_refs, ("skills/release-readiness.md", "skills/pm-core.md"))

    def test_unknown_events_stay_on_generic_route(self) -> None:
        route = route_for_event(PROJECT_ROOT, make_event("unhandled_event"))

        self.assertEqual(route.stage, "generic_triage")
        self.assertEqual(route.prompt_path, "prompts/actions/default_event.md")
        self.assertEqual(route.skill_refs, ("skills/pm-core.md",))


if __name__ == "__main__":
    unittest.main()
