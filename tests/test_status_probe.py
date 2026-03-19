import unittest

from github_pm_agent.status_probe import StatusProbe


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def api(self, path, params=None, method="GET"):
        return self.responses[path]


class StatusProbeTest(unittest.TestCase):
    def test_stale_pr_review_event(self) -> None:
        responses = {
            "repos/songsjun/example/pulls": [
                {
                    "number": 12,
                    "title": "Add PM workflow",
                    "html_url": "https://example.test/pr/12",
                    "draft": False,
                    "updated_at": "2026-03-16T00:00:00Z",
                    "created_at": "2026-03-15T00:00:00Z",
                    "user": {"login": "alice"},
                    "requested_reviewers": [{"login": "bob"}],
                }
            ],
            "repos/songsjun/example/pulls/12/reviews": [],
            "repos/songsjun/example/issues": [],
        }
        probe = StatusProbe(
            FakeClient(responses),
            "songsjun/example",
            {
                "engine": {
                    "stale_pr_review_hours": 1,
                    "blocked_issue_stale_hours": 9999,
                }
            },
        )
        events = probe.scan()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "stale_pr_review")
        self.assertEqual(events[0].target_number, 12)


if __name__ == "__main__":
    unittest.main()

