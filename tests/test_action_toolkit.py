import tempfile
import unittest
from pathlib import Path

from github_pm_agent.actions import GitHubActionToolkit


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def issue_assignees_add(self, number, users):
        self.calls.append(("issue_assignees_add", number, list(users)))
        return {"ok": True, "number": number}

    def pull_request_reviewers_request(self, number, reviewers):
        self.calls.append(("pull_request_reviewers_request", number, list(reviewers)))
        return {"ok": True, "number": number}

    def issue_state_update(self, number, state):
        self.calls.append(("issue_state_update", number, state))
        return {"ok": True, "number": number, "state": state}

    def pull_request_state_update(self, number, state):
        self.calls.append(("pull_request_state_update", number, state))
        return {"ok": True, "number": number, "state": state}


class GitHubActionToolkitTest(unittest.TestCase):
    def test_assign_dry_run_records_without_client_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            toolkit = GitHubActionToolkit(client, Path(tmp), dry_run=True)

            action = toolkit.assign("issue", 12, ["alice"])

            self.assertEqual(action["action_type"], "assign")
            self.assertEqual(action["users"], ["alice"])
            self.assertEqual(client.calls, [])

    def test_request_review_executes_client_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            toolkit = GitHubActionToolkit(client, Path(tmp), dry_run=False)

            action = toolkit.request_review(34, ["bob", "carol"])

            self.assertEqual(client.calls, [("pull_request_reviewers_request", 34, ["bob", "carol"])])
            self.assertEqual(action["result"]["ok"], True)

    def test_set_state_routes_issue_and_pull_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            toolkit = GitHubActionToolkit(client, Path(tmp), dry_run=False)

            issue_action = toolkit.set_state("issue", 10, "closed")
            pr_action = toolkit.set_state("pull_request", 11, "open")

            self.assertEqual(
                client.calls,
                [
                    ("issue_state_update", 10, "closed"),
                    ("pull_request_state_update", 11, "open"),
                ],
            )
            self.assertEqual(issue_action["result"]["state"], "closed")
            self.assertEqual(pr_action["result"]["state"], "open")
