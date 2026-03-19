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

    def issue_assignees_remove(self, number, users):
        self.calls.append(("issue_assignees_remove", number, list(users)))
        return {"ok": True, "number": number}

    def pull_request_reviewers_request(self, number, reviewers):
        self.calls.append(("pull_request_reviewers_request", number, list(reviewers)))
        return {"ok": True, "number": number}

    def pull_request_reviewers_remove(self, number, reviewers):
        self.calls.append(("pull_request_reviewers_remove", number, list(reviewers)))
        return {"ok": True, "number": number}

    def pull_request_mark_draft(self, number):
        self.calls.append(("pull_request_mark_draft", number))
        return {"ok": True, "number": number}

    def pull_request_mark_ready(self, number):
        self.calls.append(("pull_request_mark_ready", number))
        return {"ok": True, "number": number}

    def pull_request_merge(self, number, params=None):
        self.calls.append(("pull_request_merge", number, dict(params or {})))
        return {"ok": True, "number": number}

    def issue_update(self, number, **fields):
        self.calls.append(("issue_update", number, dict(fields)))
        return {"ok": True, "number": number, "fields": dict(fields)}

    def rerun_workflow_run(self, run_id):
        self.calls.append(("rerun_workflow_run", run_id))
        return {"ok": True, "run_id": run_id}

    def cancel_workflow_run(self, run_id):
        self.calls.append(("cancel_workflow_run", run_id))
        return {"ok": True, "run_id": run_id}

    def create_release(self, **fields):
        self.calls.append(("create_release", dict(fields)))
        return {"ok": True, "fields": dict(fields)}

    def create_discussion(self, repository_id, category_id, title, body):
        self.calls.append(("create_discussion", repository_id, category_id, title, body))
        return {"ok": True, "title": title}

    def update_discussion(self, discussion_id, title="", body="", category_id=""):
        self.calls.append(("update_discussion", discussion_id, title, body, category_id))
        return {"ok": True, "discussion_id": discussion_id}

    def update_project_v2_item_field_value(self, project_id, item_id, field_id, value):
        self.calls.append(("update_project_v2_item_field_value", project_id, item_id, field_id, dict(value)))
        return {"ok": True, "project_id": project_id}

    def pull_request_review_submit(self, number, decision, body="", commit_id=""):
        self.calls.append(("pull_request_review_submit", number, decision, body, commit_id))
        return {"ok": True, "number": number, "decision": decision}

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

    def test_additional_actions_execute_client_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            toolkit = GitHubActionToolkit(client, Path(tmp), dry_run=False)

            toolkit.unassign("issue", 1, ["alice"])
            toolkit.remove_reviewers(2, ["bob"])
            toolkit.mark_pull_request_draft(3)
            toolkit.mark_pull_request_ready(4)
            toolkit.merge_pull_request(5, {"merge_method": "squash"})
            toolkit.edit("issue", 6, {"title": "new title", "body": "new body"})
            toolkit.set_milestone("issue", 7, 11)
            toolkit.rerun_workflow(8)
            toolkit.cancel_workflow(9)
            toolkit.submit_review_decision(10, "approve", body="looks good")
            toolkit.create_release(tag_name="v1.2.3", name="Release 1.2.3")
            toolkit.create_discussion("repo-id", "cat-id", "Discussion", "Body")
            toolkit.update_discussion("disc-id", title="Updated")
            toolkit.update_project_field("proj-id", "item-id", "field-id", {"text": "done"})

            self.assertEqual(
                client.calls,
                [
                    ("issue_assignees_remove", 1, ["alice"]),
                    ("pull_request_reviewers_remove", 2, ["bob"]),
                    ("pull_request_mark_draft", 3),
                    ("pull_request_mark_ready", 4),
                    ("pull_request_merge", 5, {"merge_method": "squash"}),
                    ("issue_update", 6, {"title": "new title", "body": "new body"}),
                    ("issue_update", 7, {"milestone": 11}),
                    ("rerun_workflow_run", 8),
                    ("cancel_workflow_run", 9),
                    ("pull_request_review_submit", 10, "APPROVE", "looks good", ""),
                    ("create_release", {"tag_name": "v1.2.3", "name": "Release 1.2.3"}),
                    ("create_discussion", "repo-id", "cat-id", "Discussion", "Body"),
                    ("update_discussion", "disc-id", "Updated", "", ""),
                    ("update_project_v2_item_field_value", "proj-id", "item-id", "field-id", {"text": "done"}),
                ],
            )
