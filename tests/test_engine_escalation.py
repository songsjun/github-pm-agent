import json
import tempfile
import unittest
from pathlib import Path

from github_pm_agent.engine import EventEngine
from github_pm_agent.models import Event


def make_event() -> Event:
    return Event(
        event_id="evt-escalation",
        event_type="issue_comment",
        source="test",
        occurred_at="2026-03-19T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test",
        title="escalation",
        body="please help",
        target_kind="issue",
        target_number=7,
        metadata={},
    )


class EventEngineEscalationTest(unittest.TestCase):
    def _engine(self) -> EventEngine:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        runtime_dir = root / "runtime"
        return EventEngine(
            {"_project_root": str(root), "engine": {"supervisor_enabled": False}},
            object(),
            object(),
            runtime_dir,
        )

    def test_make_plan_sets_escalation_defaults(self) -> None:
        engine = self._engine()

        plan = engine.make_plan(
            should_act=False,
            reason="observe only",
            action_type="none",
            target_kind="issue",
            target_number=7,
            message="",
        )

        self.assertFalse(plan["needs_human_decision"])
        self.assertEqual(plan["human_decision_reason"], "")
        self.assertEqual(plan["urgency"], "normal")
        self.assertEqual(plan["follow_up_after_hours"], 0)
        self.assertEqual(plan["evidence"], [])
        self.assertEqual(plan["options"], [])

    def test_parse_action_plan_normalizes_legacy_json(self) -> None:
        engine = self._engine()

        plan = engine.parse_action_plan(
            json.dumps(
                {
                    "should_act": True,
                    "reason": "legacy response",
                    "action_type": "comment",
                    "target": {"kind": "issue", "number": 7},
                    "message": "hello",
                    "labels_to_add": [],
                    "labels_to_remove": [],
                    "action_input": {},
                    "memory_note": "",
                    "issue_title": "",
                }
            )
        )

        self.assertFalse(plan["needs_human_decision"])
        self.assertEqual(plan["human_decision_reason"], "")
        self.assertEqual(plan["urgency"], "normal")
        self.assertEqual(plan["follow_up_after_hours"], 0)
        self.assertEqual(plan["evidence"], [])
        self.assertEqual(plan["options"], [])

    def test_finish_plan_preserves_escalation_fields(self) -> None:
        engine = self._engine()
        plan = engine.make_plan(
            should_act=False,
            reason="needs review",
            action_type="none",
            target_kind="issue",
            target_number=7,
            message="",
            needs_human_decision=True,
            human_decision_reason="requires product owner decision",
            urgency="high",
            follow_up_after_hours=24,
            evidence=["CI failed twice", "user scope is ambiguous"],
            options=["wait for human decision", {"label": "split scope"}],
        )

        result = engine.finish_plan(make_event(), plan)

        self.assertTrue(result["plan"]["needs_human_decision"])
        self.assertEqual(result["plan"]["human_decision_reason"], "requires product owner decision")
        self.assertEqual(result["plan"]["urgency"], "high")
        self.assertEqual(result["plan"]["follow_up_after_hours"], 24)
        self.assertEqual(result["plan"]["evidence"], ["CI failed twice", "user scope is ambiguous"])
        self.assertEqual(result["plan"]["options"][0], "wait for human decision")
        self.assertEqual(result["plan"]["options"][1], {"label": "split scope"})
        self.assertTrue(result["escalation"]["needs_human_decision"])
        self.assertEqual(result["escalation"]["urgency"], "high")
        self.assertFalse(result["action"]["executed"])

    def test_contract_files_include_escalation_fields(self) -> None:
        root = Path("/Users/sjunsong/Workspace/github-pm-agent")
        schema = json.loads((root / "templates/output/action_plan.schema.json").read_text(encoding="utf-8"))
        template = (root / "templates/output/action_plan.json").read_text(encoding="utf-8")

        for key in [
            "needs_human_decision",
            "human_decision_reason",
            "urgency",
            "follow_up_after_hours",
            "evidence",
            "options",
        ]:
            self.assertIn(key, schema["properties"])
            self.assertIn(f'"{key}"', template)


if __name__ == "__main__":
    unittest.main()
