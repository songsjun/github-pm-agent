import tempfile
import unittest
from pathlib import Path

from github_pm_agent.session_store import SessionStore
from github_pm_agent.utils import read_jsonl


class SessionStoreTest(unittest.TestCase):
    def test_append_turn_records_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = SessionStore(Path(tempdir))
            store.append_turn("demo", "request", "response")

            items = read_jsonl(store.path_for("demo"))
            self.assertEqual(len(items), 1)
            self.assertIn("captured_at", items[0])

    def test_recent_transcript_respects_limit_and_character_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = SessionStore(Path(tempdir))
            store.append_turn("demo", "req-1", "resp-1")
            store.append_turn("demo", "req-2", "resp-2")
            store.append_turn("demo", "req-3", "x" * 200)

            limited = store.recent_transcript("demo", limit=2, max_chars=40)
            self.assertEqual(len(limited), 1)
            self.assertEqual(limited[0]["request"], "req-3")

            window = store.recent_transcript("demo", limit=2, max_chars=500)
            self.assertEqual([item["request"] for item in window], ["req-2", "req-3"])


if __name__ == "__main__":
    unittest.main()
