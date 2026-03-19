import unittest

from github_pm_agent.github_client import GitHubClient


class GitHubClientMethodTest(unittest.TestCase):
    def test_issue_assignees_add_uses_issue_assignees_endpoint(self) -> None:
        client = GitHubClient("/opt/homebrew/bin/gh", "acme/widgets")
        calls = []

        def fake_api(path, params=None, method="GET"):
            calls.append((path, params, method))
            return {"ok": True}

        client.api = fake_api  # type: ignore[method-assign]

        client.issue_assignees_add(9, ["alice", "bob"])

        self.assertEqual(
            calls,
            [
                (
                    "repos/acme/widgets/issues/9/assignees",
                    {"assignees[]": ["alice", "bob"]},
                    "POST",
                )
            ],
        )

    def test_review_request_and_state_methods_use_expected_endpoints(self) -> None:
        client = GitHubClient("/opt/homebrew/bin/gh", "acme/widgets")
        calls = []

        def fake_api(path, params=None, method="GET"):
            calls.append((path, params, method))
            return {"ok": True}

        client.api = fake_api  # type: ignore[method-assign]

        client.pull_request_reviewers_request(12, ["reviewer1"])
        client.issue_state_update(7, "closed")
        client.pull_request_state_update(8, "open")

        self.assertEqual(
            calls,
            [
                (
                    "repos/acme/widgets/pulls/12/requested_reviewers",
                    {"reviewers[]": ["reviewer1"]},
                    "POST",
                ),
                ("repos/acme/widgets/issues/7", {"state": "closed"}, "PATCH"),
                ("repos/acme/widgets/pulls/8", {"state": "open"}, "PATCH"),
            ],
        )
