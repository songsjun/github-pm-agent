import unittest
import subprocess

from github_pm_agent.poller import GitHubPoller


class FakeClient:
    def __init__(self, api_pages=None, graphql_pages=None, api_responses=None):
        self.api_pages = api_pages or {}
        self.graphql_pages = graphql_pages or {}
        self.api_responses = api_responses or {}
        self.api_page_counts = {}
        self.graphql_page_counts = {}
        self.review_calls = []
        self.api_calls = []

    def api(self, path, params=None, method="GET"):
        self.api_calls.append((path, params or {}, method))
        return self.api_responses.get(path, {})

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
    def test_notifications_projects_and_milestones_emit_events(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/notifications": [
                    [
                        {
                            "id": "n1",
                            "reason": "mention",
                            "updated_at": "2026-03-19T10:01:00Z",
                            "unread": True,
                            "subject": {
                                "title": "Please take a look",
                                "type": "PullRequest",
                                "url": "https://api.github.test/repos/acme/widgets/pulls/7",
                                "latest_comment_url": "https://api.github.test/repos/acme/widgets/issues/comments/1",
                            },
                        }
                    ]
                ],
                "repos/acme/widgets/milestones": [
                    [
                        {
                            "id": 4,
                            "number": 2,
                            "title": "v1.0",
                            "description": "release milestone",
                            "updated_at": "2026-03-19T10:02:00Z",
                            "html_url": "https://example.test/milestones/2",
                            "state": "open",
                            "open_issues": 3,
                            "closed_issues": 1,
                        }
                    ]
                ],
            },
            graphql_pages={
                (("data", "repository", "projectsV2"), None, False): [
                    [
                        {
                            "id": "PVT_1",
                            "number": 9,
                            "title": "Roadmap",
                            "shortDescription": "weekly board",
                            "updatedAt": "2026-03-19T10:03:00Z",
                            "closed": False,
                            "url": "https://example.test/projects/9",
                        }
                    ]
                ]
            },
        )
        poller = GitHubPoller(client, "acme/widgets", "main", ["@pm"])

        mention_events = poller._poll_notifications(since)
        project_events = poller._poll_projects(since)
        milestone_events = poller._poll_milestones(since)

        self.assertEqual([event.event_type for event in mention_events], ["mention"])
        self.assertEqual(mention_events[0].target_kind, "pull_request")
        self.assertEqual([event.event_type for event in project_events], ["project_changed"])
        self.assertEqual([event.event_type for event in milestone_events], ["milestone_changed"])

    def test_poll_ignores_project_scope_errors(self) -> None:
        since = "2026-03-19T10:00:00Z"

        class ProjectScopeErrorClient(FakeClient):
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
                if tuple(connection_path) == ("data", "repository", "projectsV2"):
                    raise subprocess.CalledProcessError(
                        1,
                        ["gh", "api", "graphql"],
                        stderr="GraphQL: INSUFFICIENT_SCOPES: requires read:project",
                    )
                return super().iter_graphql_nodes(
                    query,
                    variables,
                    connection_path=connection_path,
                    cursor_variable=cursor_variable,
                    page_size_variable=page_size_variable,
                    page_size=page_size,
                    reverse=reverse,
                )

        poller = GitHubPoller(ProjectScopeErrorClient(), "acme/widgets", "main", [])

        self.assertEqual(poller.poll(since), [])

    def test_ready_to_code_issue_opening_routes_to_issue_coding(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/issues": [
                    [
                        {
                            "id": 101,
                            "number": 7,
                            "title": "Implement app shell",
                            "body": "issue body",
                            "created_at": "2026-03-19T10:01:00Z",
                            "updated_at": "2026-03-19T10:01:00Z",
                            "html_url": "https://example.test/issues/7",
                            "state": "open",
                            "state_reason": None,
                            "labels": [{"name": "frontend"}, {"name": "ready-to-code"}],
                            "user": {"login": "pm"},
                        },
                        {
                            "id": 102,
                            "number": 8,
                            "title": "Plain issue",
                            "body": "plain body",
                            "created_at": "2026-03-19T10:02:00Z",
                            "updated_at": "2026-03-19T10:02:00Z",
                            "html_url": "https://example.test/issues/8",
                            "state": "open",
                            "state_reason": None,
                            "labels": [{"name": "bug"}],
                            "user": {"login": "pm"},
                        },
                        {
                            "id": 103,
                            "number": 9,
                            "title": "Edited ready issue",
                            "body": "edited body",
                            "created_at": "2026-03-19T09:00:00Z",
                            "updated_at": "2026-03-19T10:03:00Z",
                            "html_url": "https://example.test/issues/9",
                            "state": "open",
                            "state_reason": None,
                            "labels": [{"name": "ready-to-code"}],
                            "user": {"login": "pm"},
                        },
                    ]
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_issues(since)

        self.assertEqual([event.event_type for event in events], ["issue_coding", "issue_changed"])
        self.assertEqual(events[0].target_number, 7)
        self.assertEqual(events[1].target_number, 8)

    def test_ready_to_code_label_event_routes_to_issue_coding(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/issues/events": [
                    [
                        {
                            "id": 501,
                            "event": "labeled",
                            "created_at": "2026-03-19T10:05:00Z",
                            "actor": {"login": "pm"},
                            "label": {"name": "ready-to-code"},
                            "issue": {
                                "number": 12,
                                "html_url": "https://example.test/issues/12",
                                "labels": [{"name": "frontend"}, {"name": "ready-to-code"}],
                            },
                        },
                        {
                            "id": 502,
                            "event": "labeled",
                            "created_at": "2026-03-19T10:06:00Z",
                            "actor": {"login": "pm"},
                            "label": {"name": "bug"},
                            "issue": {
                                "number": 13,
                                "html_url": "https://example.test/issues/13",
                                "labels": [{"name": "bug"}],
                            },
                        },
                    ]
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_issue_events(since)

        self.assertEqual([event.event_type for event in events], ["issue_coding", "issue_event_labeled"])
        self.assertEqual(events[0].metadata["label"], "ready-to-code")
        self.assertEqual(events[0].metadata["labels"], ["frontend", "ready-to-code"])

    def test_ready_to_code_label_on_closed_issue_does_not_route_to_issue_coding(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/issues/events": [
                    [
                        {
                            "id": 503,
                            "event": "labeled",
                            "created_at": "2026-03-19T10:07:00Z",
                            "actor": {"login": "pm"},
                            "label": {"name": "ready-to-code"},
                            "issue": {
                                "number": 14,
                                "state": "closed",
                                "html_url": "https://example.test/issues/14",
                                "labels": [{"name": "ready-to-code"}],
                            },
                        }
                    ]
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_issue_events(since)

        self.assertEqual([event.event_type for event in events], ["issue_event_labeled"])

    def test_poll_reraises_non_scope_project_errors(self) -> None:
        since = "2026-03-19T10:00:00Z"

        class ProjectFailureClient(FakeClient):
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
                if tuple(connection_path) == ("data", "repository", "projectsV2"):
                    raise subprocess.CalledProcessError(
                        1,
                        ["gh", "api", "graphql"],
                        stderr="GraphQL: something else went wrong",
                    )
                return super().iter_graphql_nodes(
                    query,
                    variables,
                    connection_path=connection_path,
                    cursor_variable=cursor_variable,
                    page_size_variable=page_size_variable,
                    page_size=page_size,
                    reverse=reverse,
                )

        poller = GitHubPoller(ProjectFailureClient(), "acme/widgets", "main", [])

        with self.assertRaises(subprocess.CalledProcessError):
            poller.poll(since)

    def test_repo_events_capture_push_branch_and_release_signals(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/events": [
                    [
                        {
                            "id": 1,
                            "type": "PushEvent",
                            "created_at": "2026-03-19T10:01:00Z",
                            "actor": {"login": "alice"},
                            "repo": {"html_url": "https://example.test/acme/widgets"},
                            "payload": {
                                "ref": "refs/heads/main",
                                "forced": False,
                                "size": 1,
                                "before": "abc",
                                "head": "def",
                                "commits": [{"message": "update docs"}],
                            },
                        },
                        {
                            "id": 2,
                            "type": "CreateEvent",
                            "created_at": "2026-03-19T10:02:00Z",
                            "actor": {"login": "bob"},
                            "repo": {"html_url": "https://example.test/acme/widgets"},
                            "payload": {"ref_type": "branch", "ref": "feature-x"},
                        },
                        {
                            "id": 3,
                            "type": "DeleteEvent",
                            "created_at": "2026-03-19T10:03:00Z",
                            "actor": {"login": "carol"},
                            "repo": {"html_url": "https://example.test/acme/widgets"},
                            "payload": {"ref_type": "branch", "ref": "old-branch"},
                        },
                        {
                            "id": 4,
                            "type": "ReleaseEvent",
                            "created_at": "2026-03-19T10:04:00Z",
                            "actor": {"login": "dana"},
                            "repo": {"html_url": "https://example.test/acme/widgets"},
                            "payload": {"release": {"name": "v1.0", "tag_name": "v1.0", "draft": False, "prerelease": False}},
                        },
                    ]
                ]
            }
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_repo_events(since)

        self.assertEqual([event.event_type for event in events], ["push", "branch_ref_created", "branch_ref_deleted", "release_published"])
        self.assertEqual(events[0].target_kind, "branch")
        self.assertEqual(events[3].target_kind, "release")

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

    def test_commit_signals_emit_failed_status_and_check_runs(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/commits": [
                    [
                        {
                            "sha": "abc123",
                            "commit": {"author": {"date": "2026-03-19T10:05:00Z"}, "message": "ship"},
                            "author": {"login": "alice"},
                            "html_url": "https://example.test/commit/abc123",
                        }
                    ]
                ]
            },
            api_responses={
                "repos/acme/widgets/commits/abc123/status": {
                    "state": "failure",
                    "context": "ci/test",
                    "statuses": [{"context": "ci/test"}],
                },
                "repos/acme/widgets/commits/abc123/check-runs": {
                    "check_runs": [
                        {
                            "id": 7,
                            "name": "unit tests",
                            "status": "completed",
                            "conclusion": "failure",
                            "app": {"slug": "github-actions"},
                            "html_url": "https://example.test/check/7",
                        }
                    ]
                },
            },
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        events = poller._poll_commit_signals(since)

        self.assertEqual([event.event_type for event in events], ["commit_status_failed", "check_run_failed"])
        self.assertEqual(events[0].metadata["context"], "ci/test")
        self.assertEqual(events[1].metadata["name"], "unit tests")

    def test_deployments_and_releases_emit_signals(self) -> None:
        since = "2026-03-19T10:00:00Z"
        client = FakeClient(
            api_pages={
                "repos/acme/widgets/deployments": [
                    [
                        {
                            "id": 55,
                            "created_at": "2026-03-19T10:10:00Z",
                            "task": "deploy",
                            "creator": {"login": "alice"},
                            "html_url": "https://example.test/deploy/55",
                            "environment": {"name": "production"},
                            "ref": "main",
                            "sha": "abc",
                        }
                    ]
                ],
                "repos/acme/widgets/releases": [
                    [
                        {
                            "id": 77,
                            "created_at": "2026-03-19T10:11:00Z",
                            "published_at": "2026-03-19T10:12:00Z",
                            "name": "v1.0",
                            "tag_name": "v1.0",
                            "author": {"login": "bob"},
                            "html_url": "https://example.test/releases/77",
                            "body": "release notes",
                            "draft": False,
                            "prerelease": False,
                        }
                    ]
                ],
            },
            api_responses={
                "repos/acme/widgets/deployments/55/statuses": [
                    {"state": "failure"}
                ],
            },
        )
        poller = GitHubPoller(client, "acme/widgets", "main", [])

        deployment_events = poller._poll_deployments(since)
        release_events = poller._poll_releases(since)

        self.assertEqual([event.event_type for event in deployment_events], ["deployment_failed"])
        self.assertEqual(deployment_events[0].metadata["environment"], "production")
        self.assertEqual([event.event_type for event in release_events], ["release_published"])
        self.assertEqual(release_events[0].metadata["tag_name"], "v1.0")

    def test_mention_detection_looks_at_title_and_body(self) -> None:
        client = FakeClient()
        poller = GitHubPoller(client, "acme/widgets", "main", ["@pm"])
        event = poller._mention_events(
            type("EventLike", (), {"event_id": "evt", "event_type": "issue_changed", "source": "issues", "occurred_at": "2026-03-19T10:00:00Z", "repo": "acme/widgets", "actor": "alice", "url": "https://example.test", "title": "Ping @pm", "body": "", "target_kind": "issue", "target_number": 1, "metadata": {}})(),
            "",
        )
        self.assertEqual(len(event), 1)
        self.assertEqual(event[0].metadata["mention"], "@pm")

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
