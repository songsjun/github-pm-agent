import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from github_pm_agent.cli import main
from github_pm_agent.config import runtime_dir
from github_pm_agent.models import Event
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.utils import write_jsonl


class QueueStoreTest(unittest.TestCase):
    def test_enqueue_and_pop(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = QueueStore(Path(tempdir))
            event = self._event()
            self.assertEqual(store.enqueue([event]), 1)
            popped = store.pop()
            self.assertIsNotNone(popped)
            assert popped is not None
            self.assertEqual(popped.event_id, "evt-1")
            self.assertEqual(popped.metadata["_queue"]["attempt"], 1)
            self.assertIsNone(store.pop())

    def test_retry_dead_requeues_latest_failures_with_incremented_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = QueueStore(Path(tempdir))
            event = self._event()
            store.enqueue([event])
            failed = store.pop()
            assert failed is not None
            store.mark_failed(failed, "boom")

            dead = store.list_dead()
            self.assertEqual(len(dead), 1)
            self.assertEqual(dead[0]["error"], "boom")
            self.assertIn("failed_at", dead[0])

            result = store.retry_dead(event_id="evt-1")
            self.assertEqual(result["requeued"], 1)
            self.assertEqual(result["skipped"], 0)
            self.assertEqual(result["event_ids"], ["evt-1"])
            self.assertEqual(store.list_dead(), [])

            pending = store.peek(limit=1)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].metadata["_queue"]["attempt"], 2)
            self.assertEqual(pending[0].metadata["_queue"]["requeued_from"], "dead")

    def test_replay_done_skips_duplicate_pending_event_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            runtime = Path(tempdir)
            store = QueueStore(runtime)
            event = self._event()
            duplicate_record = {
                "event": event.to_dict(),
                "result": {"action": "done"},
                "done_at": "2026-03-19T00:00:00Z",
            }
            write_jsonl(store.done_path, [duplicate_record, duplicate_record])

            result = store.replay_done(limit=2)
            self.assertEqual(result["requested"], 2)
            self.assertEqual(result["requeued"], 1)
            self.assertEqual(result["skipped"], 1)

            pending = store.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].metadata["_queue"]["attempt"], 2)

            remaining_done = store.list_done()
            self.assertEqual(len(remaining_done), 1)
            self.assertEqual(remaining_done[0]["event"]["event_id"], "evt-1")

    def test_cli_exposes_dead_and_retry_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = {
                "_project_root": tempdir,
                "runtime": {"state_dir": "runtime"},
            }
            store = QueueStore(runtime_dir(config))
            event = self._event()
            store.enqueue([event])
            failed = store.pop()
            assert failed is not None
            store.mark_failed(failed, "cli boom")

            app = SimpleNamespace(config=config)

            dead_payload = self._run_cli(
                [
                    "github-pm-agent",
                    "--config",
                    "ignored.json",
                    "queue",
                    "dead",
                    "--event-id",
                    "evt-1",
                ],
                app,
            )
            self.assertEqual(len(dead_payload), 1)
            self.assertEqual(dead_payload[0]["error"], "cli boom")

            retry_payload = self._run_cli(
                [
                    "github-pm-agent",
                    "--config",
                    "ignored.json",
                    "queue",
                    "retry",
                    "--event-id",
                    "evt-1",
                ],
                app,
            )
            self.assertEqual(retry_payload["requeued"], 1)
            self.assertEqual(store.list_dead(), [])
            self.assertEqual(store.list_pending()[0].metadata["_queue"]["attempt"], 2)

    def _event(self) -> Event:
        return Event(
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

    def _run_cli(self, argv, app):
        with patch("github_pm_agent.cli._app_from_args", return_value=app):
            with patch("sys.argv", argv):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main()
        self.assertEqual(exit_code, 0)
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
