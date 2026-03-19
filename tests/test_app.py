import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock, patch

from github_pm_agent.app import GitHubPMAgentApp
from github_pm_agent.models import Event


def make_event(event_id: str, number: int) -> Event:
    return Event(
        event_id=event_id,
        event_type="issue_comment",
        source="issues",
        occurred_at="2026-03-19T10:00:00Z",
        repo="acme/widgets",
        actor="alice",
        url=f"https://example.test/issues/{number}",
        title=f"Issue #{number}",
        body="hello",
        target_kind="issue",
        target_number=number,
        metadata={},
    )


class GitHubPMAgentAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_root = Path(self.temp_dir.name)
        self.config = {
            "_project_root": str(self.project_root),
            "github": {
                "repo": "acme/widgets",
                "gh_path": "gh",
                "default_branch": "main",
                "mentions": ["@pm"],
            },
            "runtime": {"state_dir": "runtime"},
            "engine": {"dry_run": True, "continue_on_error": True},
        }

    def _build_app(
        self,
        *,
        queue: Optional[Mock] = None,
        engine: Optional[Mock] = None,
    ) -> GitHubPMAgentApp:
        queue = queue or Mock()
        engine = engine or Mock()
        client = Mock()
        prompts = object()
        sessions = object()
        ai = object()
        actions = object()
        with (
            patch("github_pm_agent.app.GitHubClient", return_value=client),
            patch("github_pm_agent.app.QueueStore", return_value=queue),
            patch("github_pm_agent.app.PromptLibrary", return_value=prompts),
            patch("github_pm_agent.app.SessionStore", return_value=sessions),
            patch("github_pm_agent.app.AIAdapterManager", return_value=ai),
            patch("github_pm_agent.app.GitHubActionToolkit", return_value=actions),
            patch("github_pm_agent.app.EventEngine", return_value=engine),
        ):
            return GitHubPMAgentApp(self.config, self.project_root)

    def test_poll_writes_cursor_and_reports_counts(self) -> None:
        queue = Mock()
        queue.enqueue.return_value = 3
        app = self._build_app(queue=queue)
        polled_events = [make_event("event-1", 1), make_event("event-2", 2)]
        synthetic_events = [make_event("event-3", 3)]
        poller = Mock()
        poller.poll.return_value = polled_events
        probe = Mock()
        probe.scan.return_value = synthetic_events
        app.repo_runtimes[0].poller = poller
        app.repo_runtimes[0].probe = probe

        with patch("github_pm_agent.app.utc_now_iso", return_value="2026-03-19T12:00:00Z"):
            result = app.poll()

        self.assertEqual(
            result,
            {
                "since": "1970-01-01T00:00:00Z",
                "events_found": 2,
                "synthetic_events_found": 1,
                "events_enqueued": 3,
            },
        )
        queue.enqueue.assert_called_once_with(polled_events + synthetic_events)
        cursor = json.loads((app.runtime_dir / "cursors.json").read_text(encoding="utf-8"))
        self.assertEqual(cursor, {"since": "2026-03-19T12:00:00Z"})

    def test_cycle_marks_done_and_failed_when_continue_on_error(self) -> None:
        queue = Mock()
        engine = Mock()
        event_one = make_event("event-1", 1)
        event_two = make_event("event-2", 2)
        queue.pop.side_effect = [event_one, event_two, None]
        engine.process.side_effect = [
            {"plan": {"should_act": True}},
            RuntimeError("boom"),
        ]
        app = self._build_app(queue=queue, engine=engine)
        app.poll = Mock(return_value={"events_enqueued": 2})

        result = app.cycle()

        self.assertEqual(result["poll"], {"events_enqueued": 2})
        self.assertEqual(len(result["processed"]), 1)
        queue.mark_done.assert_called_once()
        queue.mark_failed.assert_called_once_with(event_two, "boom")

    def test_cycle_raises_when_continue_on_error_disabled(self) -> None:
        self.config["engine"]["continue_on_error"] = False
        queue = Mock()
        engine = Mock()
        event = make_event("event-1", 1)
        queue.pop.side_effect = [event]
        engine.process.side_effect = RuntimeError("boom")
        app = self._build_app(queue=queue, engine=engine)
        app.poll = Mock(return_value={"events_enqueued": 1})

        with self.assertRaisesRegex(RuntimeError, "boom"):
            app.cycle()

        queue.mark_failed.assert_called_once_with(event, "boom")

    def test_poll_aggregates_multiple_repos_and_followups(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["github"]["repos"] = ["acme/widgets", "acme/widgets-2"]
        queue = Mock()
        queue.enqueue.return_value = 4
        client_one = Mock()
        client_two = Mock()
        poller_one = Mock()
        poller_two = Mock()
        probe_one = Mock()
        probe_two = Mock()
        followup_event = make_event("follow-1", 99)
        poller_one.poll.return_value = [make_event("event-1", 1)]
        poller_two.poll.return_value = [make_event("event-2", 2)]
        probe_one.scan.return_value = []
        probe_two.scan.return_value = [make_event("event-3", 3)]
        engine_one = Mock()
        engine_one.memory_loop = SimpleNamespace(
            due_followup_events=Mock(return_value=[followup_event]),
            analytics_snapshot=Mock(return_value={}),
        )
        engine_two = Mock()
        engine_two.memory_loop = SimpleNamespace(
            due_followup_events=Mock(return_value=[]),
            analytics_snapshot=Mock(return_value={}),
        )

        with (
            patch("github_pm_agent.app.GitHubClient", side_effect=[client_one, client_two]),
            patch("github_pm_agent.app.QueueStore", return_value=queue),
            patch("github_pm_agent.app.PromptLibrary", return_value=object()),
            patch("github_pm_agent.app.SessionStore", return_value=object()),
            patch("github_pm_agent.app.AIAdapterManager", return_value=object()),
            patch("github_pm_agent.app.GitHubActionToolkit", side_effect=[object(), object()]),
            patch("github_pm_agent.app.EventEngine", side_effect=[engine_one, engine_two]),
            patch("github_pm_agent.app.GitHubPoller", side_effect=[poller_one, poller_two]),
            patch("github_pm_agent.app.StatusProbe", side_effect=[probe_one, probe_two]),
            patch("github_pm_agent.app.utc_now_iso", return_value="2026-03-19T12:00:00Z"),
        ):
            app = GitHubPMAgentApp(config, self.project_root)
            result = app.poll()

        self.assertEqual(result["events_found"], 2)
        self.assertEqual(result["synthetic_events_found"], 1)
        self.assertEqual(result["followup_events_found"], 1)
        self.assertEqual(result["events_enqueued"], 4)
        self.assertEqual(result["repositories"][0]["repo"], "acme/widgets")
        self.assertEqual(result["repositories"][1]["repo"], "acme/widgets-2")
        queue.enqueue.assert_called_once()
