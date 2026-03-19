from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from github_pm_agent.utils import append_jsonl


class GitHubActionToolkit:
    def __init__(self, client: Any, runtime_dir: Path, dry_run: bool = True) -> None:
        self.client = client
        self.runtime_dir = runtime_dir
        self.dry_run = dry_run
        self.outbox_path = runtime_dir / "outbox.jsonl"

    def _record(self, action: Dict[str, Any]) -> None:
        append_jsonl(self.outbox_path, action)

    def _record_and_execute(self, action: Dict[str, Any], executor: Optional[Any] = None) -> Dict[str, Any]:
        self._record(action)
        if self.dry_run or executor is None:
            return action
        result = executor()
        if result is not None:
            action["result"] = result
        return action

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        action = {
            "action_type": "comment",
            "target_kind": target_kind,
            "target_number": target_number,
            "message": message,
            "dry_run": self.dry_run,
        }
        if target_kind in {"issue", "pull_request"} and target_number:
            return self._record_and_execute(action, lambda: self.client.issue_comment(target_number, message))
        return self._record_and_execute(action)

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        action = {
            "action_type": "comment",
            "target_kind": "discussion",
            "target_number": number,
            "discussion_id": discussion_id,
            "message": message,
            "dry_run": self.dry_run,
        }
        return self._record_and_execute(action, lambda: self.client.add_discussion_comment(discussion_id, message))

    def add_labels(self, number: int, labels: Iterable[str]) -> Dict[str, Any]:
        labels = [label for label in labels if label]
        action = {
            "action_type": "add_labels",
            "target_kind": "issue",
            "target_number": number,
            "labels": labels,
            "dry_run": self.dry_run,
        }
        return self._record_and_execute(action, (lambda: self.client.issue_labels_add(number, labels)) if labels else None)

    def remove_labels(self, number: int, labels: Iterable[str]) -> Dict[str, Any]:
        labels = [label for label in labels if label]
        action = {
            "action_type": "remove_labels",
            "target_kind": "issue",
            "target_number": number,
            "labels": labels,
            "dry_run": self.dry_run,
        }
        return self._record_and_execute(action, (lambda: self.client.issue_labels_remove(number, labels)) if labels else None)

    def create_issue(self, title: str, body: str, labels: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        labels = list(labels or [])
        action = {
            "action_type": "create_issue",
            "title": title,
            "body": body,
            "labels": labels,
            "dry_run": self.dry_run,
        }
        return self._record_and_execute(action, lambda: self.client.create_issue(title, body, labels))

    def assign(self, target_kind: str, target_number: Optional[int], users: Iterable[str]) -> Dict[str, Any]:
        users = [user for user in users if user]
        action = {
            "action_type": "assign",
            "target_kind": target_kind,
            "target_number": target_number,
            "users": users,
            "dry_run": self.dry_run,
        }
        if not target_number or not users:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.issue_assignees_add(target_number, users))

    def unassign(self, target_kind: str, target_number: Optional[int], users: Iterable[str]) -> Dict[str, Any]:
        users = [user for user in users if user]
        action = {
            "action_type": "unassign",
            "target_kind": target_kind,
            "target_number": target_number,
            "users": users,
            "dry_run": self.dry_run,
        }
        if not target_number or not users:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.issue_assignees_remove(target_number, users))

    def request_review(self, target_number: Optional[int], reviewers: Iterable[str]) -> Dict[str, Any]:
        reviewers = [reviewer for reviewer in reviewers if reviewer]
        action = {
            "action_type": "review_request",
            "target_kind": "pull_request",
            "target_number": target_number,
            "reviewers": reviewers,
            "dry_run": self.dry_run,
        }
        if not target_number or not reviewers:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.pull_request_reviewers_request(target_number, reviewers))

    def remove_reviewers(self, target_number: Optional[int], reviewers: Iterable[str]) -> Dict[str, Any]:
        reviewers = [reviewer for reviewer in reviewers if reviewer]
        action = {
            "action_type": "remove_reviewer",
            "target_kind": "pull_request",
            "target_number": target_number,
            "reviewers": reviewers,
            "dry_run": self.dry_run,
        }
        if not target_number or not reviewers:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.pull_request_reviewers_remove(target_number, reviewers))

    def mark_pull_request_draft(self, target_number: Optional[int]) -> Dict[str, Any]:
        action = {
            "action_type": "draft",
            "target_kind": "pull_request",
            "target_number": target_number,
            "dry_run": self.dry_run,
        }
        if not target_number:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.pull_request_mark_draft(target_number))

    def mark_pull_request_ready(self, target_number: Optional[int]) -> Dict[str, Any]:
        action = {
            "action_type": "ready_for_review",
            "target_kind": "pull_request",
            "target_number": target_number,
            "dry_run": self.dry_run,
        }
        if not target_number:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.pull_request_mark_ready(target_number))

    def merge_pull_request(self, target_number: Optional[int], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        action = {
            "action_type": "merge",
            "target_kind": "pull_request",
            "target_number": target_number,
            "params": params,
            "dry_run": self.dry_run,
        }
        if not target_number:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.pull_request_merge(target_number, params))

    def edit(self, target_kind: str, target_number: Optional[int], fields: Dict[str, Any]) -> Dict[str, Any]:
        fields = {key: value for key, value in dict(fields or {}).items() if value is not None}
        action = {
            "action_type": "edit",
            "target_kind": target_kind,
            "target_number": target_number,
            "fields": fields,
            "dry_run": self.dry_run,
        }
        if not target_number or not fields:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.issue_update(target_number, **fields))

    def set_milestone(self, target_kind: str, target_number: Optional[int], milestone: Any) -> Dict[str, Any]:
        action = {
            "action_type": "milestone",
            "target_kind": target_kind,
            "target_number": target_number,
            "milestone": milestone,
            "dry_run": self.dry_run,
        }
        if not target_number or milestone is None:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.issue_update(target_number, milestone=milestone))

    def rerun_workflow(self, run_id: Optional[int]) -> Dict[str, Any]:
        action = {
            "action_type": "rerun_workflow",
            "target_kind": "workflow_run",
            "target_number": run_id,
            "dry_run": self.dry_run,
        }
        if not run_id:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.rerun_workflow_run(run_id))

    def cancel_workflow(self, run_id: Optional[int]) -> Dict[str, Any]:
        action = {
            "action_type": "cancel_workflow",
            "target_kind": "workflow_run",
            "target_number": run_id,
            "dry_run": self.dry_run,
        }
        if not run_id:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.cancel_workflow_run(run_id))

    def submit_review_decision(
        self,
        target_number: Optional[int],
        decision: str,
        body: str = "",
        commit_id: str = "",
    ) -> Dict[str, Any]:
        decision = (decision or "").strip().lower()
        action = {
            "action_type": "review_decision",
            "target_kind": "pull_request",
            "target_number": target_number,
            "decision": decision,
            "body": body,
            "commit_id": commit_id,
            "dry_run": self.dry_run,
        }
        if not target_number or decision not in {"approve", "request_changes"}:
            return self._record_and_execute(action)
        event = "APPROVE" if decision == "approve" else "REQUEST_CHANGES"
        return self._record_and_execute(
            action,
            lambda: self.client.pull_request_review_submit(target_number, event, body=body, commit_id=commit_id),
        )

    def create_release(self, **fields: Any) -> Dict[str, Any]:
        tag_name = str(fields.get("tag_name", "")).strip()
        action = {
            "action_type": "create_release",
            "target_kind": "repo",
            "target_number": None,
            "fields": {key: value for key, value in dict(fields).items() if value is not None},
            "dry_run": self.dry_run,
        }
        if not tag_name:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.create_release(**fields))

    def create_discussion(
        self,
        repository_id: str,
        category_id: str,
        title: str,
        body: str,
    ) -> Dict[str, Any]:
        action = {
            "action_type": "create_discussion",
            "target_kind": "discussion",
            "target_number": None,
            "repository_id": repository_id,
            "category_id": category_id,
            "title": title,
            "body": body,
            "dry_run": self.dry_run,
        }
        if not repository_id or not category_id or not title:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.create_discussion(repository_id, category_id, title, body))

    def update_discussion(
        self,
        discussion_id: str,
        title: str = "",
        body: str = "",
        category_id: str = "",
    ) -> Dict[str, Any]:
        action = {
            "action_type": "update_discussion",
            "target_kind": "discussion",
            "target_number": None,
            "discussion_id": discussion_id,
            "title": title,
            "body": body,
            "category_id": category_id,
            "dry_run": self.dry_run,
        }
        if not discussion_id or not (title or body or category_id):
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.update_discussion(discussion_id, title=title, body=body, category_id=category_id))

    def update_project_field(self, project_id: str, item_id: str, field_id: str, value: Dict[str, Any]) -> Dict[str, Any]:
        action = {
            "action_type": "project",
            "target_kind": "project_item",
            "target_number": None,
            "project_id": project_id,
            "item_id": item_id,
            "field_id": field_id,
            "value": dict(value or {}),
            "dry_run": self.dry_run,
        }
        if not project_id or not item_id or not field_id or not value:
            return self._record_and_execute(action)
        return self._record_and_execute(action, lambda: self.client.update_project_v2_item_field_value(project_id, item_id, field_id, value))

    def set_state(self, target_kind: str, target_number: Optional[int], state: str) -> Dict[str, Any]:
        action = {
            "action_type": "state",
            "target_kind": target_kind,
            "target_number": target_number,
            "state": state,
            "dry_run": self.dry_run,
        }
        if not target_number or not state:
            return self._record_and_execute(action)
        if target_kind == "pull_request":
            return self._record_and_execute(action, lambda: self.client.pull_request_state_update(target_number, state))
        return self._record_and_execute(action, lambda: self.client.issue_state_update(target_number, state))
