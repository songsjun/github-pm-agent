import unittest

from github_pm_agent.poller import GitHubPoller


class FakeClient:
    def __init__(self, api_pages=None, graphql_pages=None):
        self.api_pages = api_pages or {}
        self.graphql_pages = graphql_pages or {}
        self.api_page_counts = {}
        self.graphql_page_counts = {}
        self.review_calls = []

    def iter_api_pages(self, path, params=None, method="GET", list_key=None, per_page=100):
        self.api_page_counts[path] = 0
        if path.endswith("/reviews"):
            self.review_calls.append(path)
        for page in self.api_pages.get(path, []):
            if not page:
                return
            self.api_page_counts[path] += 1
            yield list(page)

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
        key = (tuple(connection_path), (variables or {}).get("discussionId"), reverse)
        self.graphql_page_counts[key] = 0
        for page in self.graphql_pages.get(key, []):
            self.graphql_page_counts[key] += 1
            for node in page:
                yield dict(node)


class GitHubPollerTest(unittest.TestCase):
    def test_issue_comments_paginate_and_exclude_cutoff_timestamp(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/issues/comments": [
                    [
                        {
                            "id": 1,
                            "updated_at": "2026-03-19T10:00:00Z",
                            "issue_url": "https://api.github.test/repos/acme/widgets/issues/11",
                            "html_url": "https://example.test/issues/11#issuecomment-1",
                            "body": "old",
                            "user": {"login": "alice"},
                        },
                        {
                            "id": 2,
                            "updated_at": "2026-03-19T10:01:00Z",
                            "issue_url": "https://api.github.test/repos/acme/widgets/issues/11",
                            "html_url": "https://example.test/issues/11#issuecomment-2",
                            "body": "newer",
                            "user": {"login": "bob"},
                        },
                    ],
                    [
                        {
                            "id": 3,
                            "updated_at": "2026-03-19T10:02:00Z",
                            "issue_url": "https://api.github.test/repos/acme/widgets/issues/12",
                            "html_url": "https://example.test/issues/12#issuecomment-3",
                            "body": "newest",
                            "user": {"login": "carol"},
                        }
                    ],
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_issue_comments(since)

        self.assertEqual([event.target_number for event in events], [11, 12])
        self.assertEqual(client.api_page_counts["repos/acme/widgets/issues/comments"], 2)

    def test_issue_events_stop_after_old_page(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/issues/events": [
                    [
                        {
                            "id": 10,
                            "created_at": "2026-03-19T10:05:00Z",
                            "event": "labeled",
                            "actor": {"login": "alice"},
                            "issue": {"number": 1, "html_url": "https://example.test/issues/1"},
                        }
                    ],
                    [
                        {
                            "id": 11,
                            "created_at": "2026-03-19T10:00:00Z",
                            "event": "assigned",
                            "actor": {"login": "bob"},
                            "issue": {"number": 2, "html_url": "https://example.test/issues/2"},
                        }
                    ],
                    [
                        {
                            "id": 12,
                            "created_at": "2026-03-19T10:07:00Z",
                            "event": "closed",
                            "actor": {"login": "carol"},
                            "issue": {"number": 3, "html_url": "https://example.test/issues/3"},
                        }
                    ],
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_issue_events(since)

        self.assertEqual([event.target_number for event in events], [1])
        self.assertEqual(client.api_page_counts["repos/acme/widgets/issues/events"], 2)

    def test_pull_request_reviews_paginate_recent_prs_and_reviews(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/pulls": [
                    [
                        {
                            "number": 7,
                            "updated_at": "2026-03-19T10:05:00Z",
                            "html_url": "https://example.test/pulls/7",
                        },
                        {
                            "number": 8,
                            "updated_at": "2026-03-19T10:00:00Z",
                            "html_url": "https://example.test/pulls/8",
                        },
                    ],
                    [
                        {
                            "number": 9,
                            "updated_at": "2026-03-19T09:59:00Z",
                            "html_url": "https://example.test/pulls/9",
                        }
                    ],
                    [
                        {
                            "number": 10,
                            "updated_at": "2026-03-19T10:10:00Z",
                            "html_url": "https://example.test/pulls/10",
                        }
                    ],
                ],
                "repos/acme/widgets/pulls/7/reviews": [
                    [
                        {
                            "id": 70,
                            "submitted_at": "2026-03-19T10:00:00Z",
                            "state": "COMMENTED",
                            "body": "old",
                            "user": {"login": "alice"},
                        },
                        {
                            "id": 71,
                            "submitted_at": "2026-03-19T10:01:00Z",
                            "state": "APPROVED",
                            "body": "ship it",
                            "user": {"login": "bob"},
                        },
                    ],
                    [
                        {
                            "id": 72,
                            "submitted_at": "2026-03-19T10:02:00Z",
                            "state": "CHANGES_REQUESTED",
                            "body": "needs work",
                            "user": {"login": "carol"},
                        }
                    ],
                ],
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_pull_request_reviews(since)

        self.assertEqual([event.metadata["state"] for event in events], ["APPROVED", "CHANGES_REQUESTED"])
        self.assertEqual(client.api_page_counts["repos/acme/widgets/pulls"], 2)
        self.assertEqual(client.review_calls, ["repos/acme/widgets/pulls/7/reviews"])

    def test_workflow_runs_scan_is_bounded(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/actions/runs": [
                    [
                        {
                            "id": 1,
                            "updated_at": "2026-03-19T10:01:00Z",
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": 101,
                            "actor": {"login": "alice"},
                            "html_url": "https://example.test/runs/1",
                        }
                    ],
                    [
                        {
                            "id": 2,
                            "updated_at": "2026-03-19T10:02:00Z",
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": 102,
                            "actor": {"login": "bob"},
                            "html_url": "https://example.test/runs/2",
                        }
                    ],
                    [
                        {
                            "id": 3,
                            "updated_at": "2026-03-19T10:03:00Z",
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "failure",
                            "run_number": 103,
                            "actor": {"login": "carol"},
                            "html_url": "https://example.test/runs/3",
                        }
                    ],
                    [
                        {
                            "id": 4,
                            "updated_at": "2026-03-19T10:04:00Z",
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": 104,
                            "actor": {"login": "dana"},
                            "html_url": "https://example.test/runs/4",
                        }
                    ],
                    [
                        {
                            "id": 5,
                            "updated_at": "2026-03-19T10:05:00Z",
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": 105,
                            "actor": {"login": "erin"},
                            "html_url": "https://example.test/runs/5",
                        }
                    ],
                    [
                        {
                            "id": 6,
                            "updated_at": "2026-03-19T10:06:00Z",
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": 106,
                            "actor": {"login": "frank"},
                            "html_url": "https://example.test/runs/6",
                        }
                    ],
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_workflow_runs(since)

        self.assertEqual([event.target_number for event in events], [101, 102, 103, 104, 105])
        self.assertEqual(client.api_page_counts["repos/acme/widgets/actions/runs"], 5)

    def test_discussions_paginate_comments_and_skip_cutoff_discussion(self) -> None:
        since = "2026-03-19T10:00:00Z"
        discussion_key = (("data", "repository", "discussions"), None, False)
        comment_key = (("data", "node", "comments"), "DISCUSSION_1", True)
        client = FakeClient(
            graphql_pages={
                discussion_key: [
                    [
                        {
                            "id": "DISCUSSION_1",
                            "number": 14,
                            "title": "Roadmap",
                            "body": "Discuss @pm",
                            "url": "https://example.test/discussions/14",
                            "updatedAt": "2026-03-19T10:05:00Z",
                            "author": {"login": "alice"},
                        },
                        {
                            "id": "DISCUSSION_2",
                            "number": 15,
                            "title": "Old thread",
                            "body": "old",
                            "url": "https://example.test/discussions/15",
                            "updatedAt": "2026-03-19T10:00:00Z",
                            "author": {"login": "bob"},
                        },
                    ]
                ],
                comment_key: [
                    [
                        {
                            "id": "COMMENT_1",
                            "body": "latest",
                            "updatedAt": "2026-03-19T10:06:00Z",
                            "author": {"login": "carol"},
                            "url": "https://example.test/discussions/14#comment-1",
                        }
                    ],
                    [
                        {
                            "id": "COMMENT_2",
                            "body": "older but still new",
                            "updatedAt": "2026-03-19T10:01:00Z",
                            "author": {"login": "dana"},
                            "url": "https://example.test/discussions/14#comment-2",
                        },
                        {
                            "id": "COMMENT_3",
                            "body": "at cutoff",
                            "updatedAt": "2026-03-19T10:00:00Z",
                            "author": {"login": "erin"},
                            "url": "https://example.test/discussions/14#comment-3",
                        },
                    ],
                ],
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", ["@pm"])

        events = poller._poll_discussions(since)

        self.assertEqual(
            [event.event_type for event in events],
            ["discussion", "mention", "discussion_comment", "discussion_comment"],
        )
        self.assertEqual(client.graphql_page_counts[comment_key], 2)

    def test_poll_deduplicates_duplicate_event_ids(self) -> None:
        since = "2026-03-19T10:00:00Z"
        duplicate_comment = {
            "id": 44,
            "updated_at": "2026-03-19T10:02:00Z",
            "issue_url": "https://api.github.test/repos/acme/widgets/issues/44",
            "html_url": "https://example.test/issues/44#issuecomment-44",
            "body": "same event twice",
            "user": {"login": "alice"},
        }
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/issues/comments": [[duplicate_comment], [duplicate_comment]],
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller.poll(since)

        self.assertEqual([event.event_type for event in events], ["issue_comment"])


if __name__ == "__main__":
    unittest.main()
