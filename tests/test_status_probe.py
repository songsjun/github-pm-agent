import unittest

from github_pm_agent.status_probe import StatusProbe


class FakeClient:
    def __init__(self, responses, graphql_responses=None):
        self.responses = responses
        self.graphql_responses = graphql_responses or {}

    def api(self, path, params=None, method="GET"):
        return self.responses.get(path, {})

    def graphql(self, query, variables=None):
        return self.graphql_responses.get(tuple(sorted((variables or {}).items())), {})

    def iter_graphql_nodes(
        self,
        query,
        variables=None,
        *,
        connection_path,
        cursor_variable,
        page_size_variable,
        page_size,
        reverse=False,
    ):
        key = tuple(sorted((variables or {}).items()))
        for node in self.graphql_responses.get(key, []):
            yield dict(node)


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

    def test_synthetic_states_cover_ci_review_discussion_release_and_docs_drift(self) -> None:
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
            "repos/songsjun/example/actions/runs": {
                "workflow_runs": [
                    {"id": 1, "conclusion": "failure", "updated_at": "2026-03-18T00:00:00Z", "name": "CI", "run_number": 1, "html_url": "https://example.test/run/1"},
                    {"id": 2, "conclusion": "failure", "updated_at": "2026-03-18T01:00:00Z", "name": "CI", "run_number": 2, "html_url": "https://example.test/run/2"},
                    {"id": 3, "conclusion": "success", "updated_at": "2026-03-18T02:00:00Z", "name": "CI", "run_number": 3, "html_url": "https://example.test/run/3"},
                ]
            },
            "repos/songsjun/example/releases": [
                {
                    "id": 77,
                    "tag_name": "v1.0",
                    "name": "v1.0",
                    "html_url": "https://example.test/releases/77",
                    "published_at": "2026-03-15T00:00:00Z",
                }
            ],
            "repos/songsjun/example/compare/v1.0...main": {
                "files": [
                    {"filename": "src/app.py"},
                ]
            },
        }
        graphql_responses = {
            tuple(sorted((("owner", "songsjun"), ("name", "example")))): [
                {
                    "id": "D1",
                    "number": 3,
                    "title": "Decide release path",
                    "body": "Should we ship now?",
                    "url": "https://example.test/discussions/3",
                    "createdAt": "2026-03-15T00:00:00Z",
                    "updatedAt": "2026-03-16T00:00:00Z",
                }
            ],
            tuple(sorted((("discussionId", "D1"),))): [
                {"id": "C1"},
                {"id": "C2"},
            ],
        }
        probe = StatusProbe(
            FakeClient(responses, graphql_responses),
            "songsjun/example",
            {
                "github": {"default_branch": "main"},
                "engine": {
                    "stale_pr_review_hours": 1,
                    "blocked_issue_stale_hours": 9999,
                }
            },
        )
        probe.client.responses["repos/songsjun/example/pulls/12/reviews"] = [
            {"state": "CHANGES_REQUESTED"},
            {"state": "APPROVED"},
        ]
        events = probe.scan()
        event_types = {event.event_type for event in events}
        self.assertIn("review_churn", event_types)
        self.assertIn("repeated_ci_instability", event_types)
        self.assertIn("stale_discussion_decision", event_types)
        self.assertIn("docs_drift_before_release", event_types)
        self.assertIn("release_readiness", event_types)


if __name__ == "__main__":
    unittest.main()
