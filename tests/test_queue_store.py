import tempfile
import unittest
from pathlib import Path

from github_pm_agent.models import Event
from github_pm_agent.queue_store import QueueStore


class QueueStoreTest(unittest.TestCase):
    def test_enqueue_and_pop(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = QueueStore(Path(tempdir))
            event = Event(
                event_id="evt-1",
                event_type="issue_comment",
                source="issue_comments",
                occurred_at="2026-03-19T00:00:00Z",
                repo="songsjun/example",
                actor="songsjun",
                url="https://example.com",
                title="hello",
                body="world",
                target_kind="issue",
                target_number=1,
            )
            self.assertEqual(store.enqueue([event]), 1)
            popped = store.pop()
            self.assertIsNotNone(popped)
            assert popped is not None
            self.assertEqual(popped.event_id, "evt-1")
            self.assertIsNone(store.pop())


if __name__ == "__main__":
    unittest.main()
