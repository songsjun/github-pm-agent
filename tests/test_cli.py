import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import List, Tuple
from unittest.mock import Mock, patch

from github_pm_agent.cli import main


class GitHubPMAgentCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = {
            "_project_root": self.temp_dir.name,
            "github": {"repo": "acme/widgets"},
            "runtime": {"state_dir": "runtime"},
        }
        self.app = SimpleNamespace(config=self.config)

    def _run_main(self, argv: List[str], *, app=None, queue=None) -> Tuple[int, str]:
        app = app or self.app
        queue = queue or Mock()
        stdout = io.StringIO()
        with (
            patch("sys.argv", argv),
            patch("github_pm_agent.cli._app_from_args", return_value=app),
            patch("github_pm_agent.cli.QueueStore", return_value=queue),
            redirect_stdout(stdout),
        ):
            code = main()
        return code, stdout.getvalue()

    def test_poll_command_prints_json_payload(self) -> None:
        app = SimpleNamespace(config=self.config, poll=Mock(return_value={"events_found": 2}))

        code, output = self._run_main(
            ["github-pm-agent", "--config", "/tmp/config.json", "poll"],
            app=app,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"events_found": 2})
        app.poll.assert_called_once_with()

    def test_queue_done_command_filters_by_event_id(self) -> None:
        queue = Mock()
        queue.list_done.return_value = [{"event": {"event_id": "evt-1"}}]

        code, output = self._run_main(
            [
                "github-pm-agent",
                "--config",
                "/tmp/config.json",
                "queue",
                "done",
                "--event-id",
                "evt-1",
            ],
            queue=queue,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), [{"event": {"event_id": "evt-1"}}])
        queue.list_done.assert_called_once_with(limit=None, event_id="evt-1")

    def test_queue_retry_command_prints_retry_summary(self) -> None:
        queue = Mock()
        queue.retry_dead.return_value = {"requeued": 2, "event_ids": ["evt-1", "evt-2"]}

        code, output = self._run_main(
            [
                "github-pm-agent",
                "--config",
                "/tmp/config.json",
                "queue",
                "retry",
                "--all",
                "--limit",
                "2",
            ],
            queue=queue,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"requeued": 2, "event_ids": ["evt-1", "evt-2"]})
        queue.retry_dead.assert_called_once_with(event_id=None, limit=2)

    def test_reconcile_command_prints_processed_payload(self) -> None:
        app = SimpleNamespace(config=self.config, reconcile=Mock(return_value={"processed": []}))

        code, output = self._run_main(
            ["github-pm-agent", "--config", "/tmp/config.json", "reconcile"],
            app=app,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"processed": []})
        app.reconcile.assert_called_once_with()

    def test_analytics_command_prints_snapshot(self) -> None:
        app = SimpleNamespace(config=self.config, analytics=Mock(return_value={"queue": {"pending": 1}}))

        code, output = self._run_main(
            ["github-pm-agent", "--config", "/tmp/config.json", "analytics"],
            app=app,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"queue": {"pending": 1}})
        app.analytics.assert_called_once_with()

    def test_daemon_command_uses_interval_and_cycles(self) -> None:
        app = SimpleNamespace(config=self.config, daemon=Mock(return_value={"cycles": 2}))

        code, output = self._run_main(
            [
                "github-pm-agent",
                "--config",
                "/tmp/config.json",
                "daemon",
                "--interval",
                "5",
                "--cycles",
                "2",
            ],
            app=app,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"cycles": 2})
        app.daemon.assert_called_once_with(interval_seconds=5.0, max_cycles=2)

    def test_webhook_command_reads_payload_file(self) -> None:
        payload_path = Path(self.temp_dir.name) / "webhook.json"
        payload_path.write_text(
            json.dumps(
                {
                    "repository": {"full_name": "acme/widgets"},
                    "action": "opened",
                    "title": "new issue",
                    "body": "hello",
                }
            ),
            encoding="utf-8",
        )
        app = SimpleNamespace(
            config=self.config,
            ingest_webhook=Mock(return_value={"events_found": 1, "events_enqueued": 1}),
        )

        code, output = self._run_main(
            [
                "github-pm-agent",
                "--config",
                "/tmp/config.json",
                "webhook",
                "--event-type",
                "issues",
                "--payload-file",
                str(payload_path),
            ],
            app=app,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"events_found": 1, "events_enqueued": 1})
        app.ingest_webhook.assert_called_once()
