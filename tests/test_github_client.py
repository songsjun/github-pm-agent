import unittest

from github_pm_agent.github_client import GitHubClient


class GitHubClientMethodTest(unittest.TestCase):
    def test_issue_assignees_add_uses_issue_assignees_endpoint(self) -> None:
        client = GitHubClient("gh", "acme/widgets")
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
        client = GitHubClient("gh", "acme/widgets")
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

    def test_remaining_action_methods_use_expected_endpoints(self) -> None:
        client = GitHubClient("gh", "acme/widgets")
        calls = []

        def fake_api(path, params=None, method="GET"):
            calls.append((path, params, method))
            return {"ok": True}

        def fake_graphql(query, variables=None):
            calls.append(("graphql", query.strip().splitlines()[0], dict(variables or {})))
            return {"ok": True}

        client.api = fake_api  # type: ignore[method-assign]
        client.graphql = fake_graphql  # type: ignore[method-assign]

        client.issue_update(1, title="new", body="body", milestone=3)
        client.issue_assignees_remove(2, ["alice"])
        client.pull_request_reviewers_remove(3, ["bob"])
        client.pull_request_mark_draft(4)
        client.pull_request_mark_ready(5)
        client.pull_request_merge(6, {"merge_method": "squash"})
        client.pull_request_review_submit(7, "APPROVE", body="LGTM")
        client.rerun_workflow_run(8)
        client.cancel_workflow_run(9)
        client.create_release(tag_name="v1.0.0", name="Release")
        client.create_discussion("repo-id", "cat-id", "Title", "Body")
        client.update_discussion("disc-id", title="Updated")
        client.update_project_v2_item_field_value("proj-id", "item-id", "field-id", {"text": "done"})

        self.assertEqual(
            calls,
            [
                ("repos/acme/widgets/issues/1", {"title": "new", "body": "body", "milestone": 3}, "PATCH"),
                ("repos/acme/widgets/issues/2/assignees", {"assignees[]": ["alice"]}, "DELETE"),
                ("repos/acme/widgets/pulls/3/requested_reviewers", {"reviewers[]": ["bob"]}, "DELETE"),
                ("repos/acme/widgets/pulls/4/convert-to-draft", None, "POST"),
                ("repos/acme/widgets/pulls/5/ready_for_review", None, "POST"),
                ("repos/acme/widgets/pulls/6/merge", {"merge_method": "squash"}, "PUT"),
                ("repos/acme/widgets/pulls/7/reviews", {"event": "APPROVE", "body": "LGTM"}, "POST"),
                ("repos/acme/widgets/actions/runs/8/rerun", None, "POST"),
                ("repos/acme/widgets/actions/runs/9/cancel", None, "POST"),
                ("repos/acme/widgets/releases", {"tag_name": "v1.0.0", "name": "Release"}, "POST"),
                ("graphql", "mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {", {"repositoryId": "repo-id", "categoryId": "cat-id", "title": "Title", "body": "Body"}),
                ("graphql", "mutation($discussionId: ID!, $title: String) {", {"discussionId": "disc-id", "title": "Updated"}),
                ("graphql", "mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $fieldValue: String!) {", {"projectId": "proj-id", "itemId": "item-id", "fieldId": "field-id", "fieldValue": "done"}),
            ],
        )
