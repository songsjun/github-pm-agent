import tempfile
import unittest
from pathlib import Path

from github_pm_agent.memory_loop import MemoryLoop
from github_pm_agent.models import ActionResult, Event
from github_pm_agent.utils import read_json, read_jsonl, write_jsonl


class MemoryLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.runtime_dir = self.root / "runtime"
        self.loop = MemoryLoop(
            self.runtime_dir,
            {
                "_project_root": str(self.root),
                "engine": {
                    "memory": {
                        "activity_batch_size": 3,
                        "min_notes_for_batch": 2,
                        "max_age_minutes": 30,
                        "lookback_notes": 24,
                        "max_distilled_items": 4,
                    }
                },
            },
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_synthesizes_only_after_batch_threshold(self) -> None:
        self._record_plan_note(
            "changes requested on PR #17 by @alice",
            recorded_at="2026-03-19T00:00:00Z",
            target_number=17,
        )
        first = self.loop.note_activity(now_iso="2026-03-19T00:00:00Z")
        self.assertFalse(first["refreshed"])

        self._record_plan_note(
            "changes requested on PR #24 by @bob",
            recorded_at="2026-03-19T00:10:00Z",
            target_number=24,
        )
        second = self.loop.note_activity(now_iso="2026-03-19T00:10:00Z")
        self.assertFalse(second["refreshed"])

        refreshed = self.loop.note_activity(now_iso="2026-03-19T00:20:00Z")
        self.assertTrue(refreshed["refreshed"])

        distilled = self.loop.distilled_path.read_text(encoding="utf-8")
        self.assertIn("Review feedback is recurring", distilled)
        self.assertIn("PR #24", distilled)

        state = read_json(self.loop.state_path, {})
        self.assertEqual(state["last_note_index"], 2)
        self.assertEqual(state["activities_since_synthesis"], 0)

    def test_synthesizes_after_time_threshold_with_single_supervisor_note(self) -> None:
        self.loop.record_supervisor_note("Keep release policy comments concise and action-oriented.")
        notes = read_jsonl(self.loop.raw_notes_path)
        notes[-1]["recorded_at"] = "2026-03-19T00:00:00Z"
        write_jsonl(self.loop.raw_notes_path, notes)

        first = self.loop.note_activity(now_iso="2026-03-19T00:00:00Z")
        self.assertFalse(first["refreshed"])

        refreshed = self.loop.note_activity(now_iso="2026-03-19T01:00:00Z")
        self.assertTrue(refreshed["refreshed"])
        self.assertIn("Supervisor signal", self.loop.distilled_path.read_text(encoding="utf-8"))

    def test_memory_refs_include_generated_artifacts(self) -> None:
        self.loop.record_supervisor_note("Keep release policy comments concise and action-oriented.")
        self._record_plan_note(
            "CI instability is showing up repeatedly: 2 workflow failure signals recently.",
            recorded_at="2026-03-19T00:00:00Z",
            target_number=17,
        )
        self._record_plan_note(
            "CI instability is showing up repeatedly: 2 workflow failure signals recently.",
            recorded_at="2026-03-19T00:20:00Z",
            target_number=18,
        )
        notes = read_jsonl(self.loop.raw_notes_path)
        notes[-2]["recorded_at"] = "2026-03-19T00:00:00Z"
        notes[-1]["recorded_at"] = "2026-03-19T00:10:00Z"
        notes[-3]["recorded_at"] = "2026-03-19T00:00:00Z"
        write_jsonl(self.loop.raw_notes_path, notes)

        self.loop.note_activity(now_iso="2026-03-19T01:00:00Z")

        refs = self.loop.memory_refs(["memory/README.md"])
        self.assertIn("runtime/memory/distilled.md", refs)
        self.assertIn("runtime/memory/policy.md", refs)
        self.assertIn("runtime/memory/trends.md", refs)
        self.assertIn("runtime/memory/retro.md", refs)

    def test_records_followup_and_emits_due_followup_event(self) -> None:
        event = self._event(target_number=42)
        plan = {
            "memory_note": "follow up on issue #42",
            "action_type": "comment",
            "target": {"kind": "issue", "number": 42},
            "follow_up_after_hours": 2,
            "needs_human_decision": True,
            "reason": "needs product decision",
        }
        result = ActionResult(True, "comment", {"kind": "issue", "number": 42}, "hello", {})
        self.loop.record_plan_result(event, plan, result)

        due_soon = self.loop.due_followup_events(now_iso="2026-03-19T01:00:00Z")
        self.assertEqual(due_soon, [])

        due_later = self.loop.due_followup_events(now_iso="2026-03-19T03:00:00Z")
        self.assertEqual(len(due_later), 1)
        self.assertEqual(due_later[0].event_type, "follow_up_due")
        self.assertEqual(due_later[0].target_number, 42)

        repeat = self.loop.due_followup_events(now_iso="2026-03-19T04:00:00Z")
        self.assertEqual(repeat, [])

    def test_analytics_snapshot_counts_signals_and_followups(self) -> None:
        self._record_plan_note(
            "changes requested on PR #17 by @alice",
            recorded_at="2026-03-19T00:00:00Z",
            target_number=17,
        )
        self.loop.record_supervisor_note("Keep release policy comments concise and action-oriented.")
        self.loop.record_plan_result(
            self._event(target_number=17),
            {
                "memory_note": "policy follow-up",
                "action_type": "none",
                "target": {"kind": "issue", "number": 17},
                "follow_up_after_hours": 1,
                "needs_human_decision": True,
                "reason": "needs human decision",
            },
            ActionResult(False, "none", {"kind": "issue", "number": 17}, "", {}),
        )

        snapshot = self.loop.analytics_snapshot(now_iso="2026-03-19T02:00:00Z")
        self.assertGreaterEqual(snapshot["notes_total"], 2)
        self.assertIn("policy", snapshot["signal_counts"])
        self.assertGreaterEqual(snapshot["followup_counts"]["scheduled"], 1)

    def _record_plan_note(self, memory_note: str, recorded_at: str, target_number: int) -> None:
        event = self._event(target_number=target_number)
        plan = {
            "memory_note": memory_note,
            "action_type": "none",
            "target": {"kind": "pull_request", "number": target_number},
        }
        result = ActionResult(False, "none", {"kind": "pull_request", "number": target_number}, "", {})
        self.loop.record_plan_result(event, plan, result)
        notes = read_jsonl(self.loop.raw_notes_path)
        notes[-1]["recorded_at"] = recorded_at
        write_jsonl(self.loop.raw_notes_path, notes)

    def _event(self, target_number: int = 17) -> Event:
        return Event(
            event_id="evt-1",
            event_type="pull_request_review",
            source="test",
            occurred_at="2026-03-19T00:00:00Z",
            repo="songsjun/example",
            actor="reviewer",
            url="https://example.test",
            title="PR feedback",
            body="",
            target_kind="pull_request",
            target_number=target_number,
            metadata={},
        )


if __name__ == "__main__":
    unittest.main()
