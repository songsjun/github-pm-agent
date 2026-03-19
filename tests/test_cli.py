import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
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
