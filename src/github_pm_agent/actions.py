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

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        action = {
            "action_type": "comment",
            "target_kind": target_kind,
            "target_number": target_number,
            "message": message,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run:
            return action
        if target_kind in {"issue", "pull_request"} and target_number:
            result = self.client.issue_comment(target_number, message)
            action["result"] = result
            return action
        return action

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        action = {
            "action_type": "comment",
            "target_kind": "discussion",
            "target_number": number,
            "discussion_id": discussion_id,
            "message": message,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run:
            return action
        result = self.client.add_discussion_comment(discussion_id, message)
        action["result"] = result
        return action

    def add_labels(self, number: int, labels: Iterable[str]) -> Dict[str, Any]:
        labels = [label for label in labels if label]
        action = {
            "action_type": "add_labels",
            "target_kind": "issue",
            "target_number": number,
            "labels": labels,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run or not labels:
            return action
        result = self.client.issue_labels_add(number, labels)
        action["result"] = result
        return action

    def remove_labels(self, number: int, labels: Iterable[str]) -> Dict[str, Any]:
        labels = [label for label in labels if label]
        action = {
            "action_type": "remove_labels",
            "target_kind": "issue",
            "target_number": number,
            "labels": labels,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run or not labels:
            return action
        self.client.issue_labels_remove(number, labels)
        return action

    def create_issue(self, title: str, body: str, labels: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        labels = list(labels or [])
        action = {
            "action_type": "create_issue",
            "title": title,
            "body": body,
            "labels": labels,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run:
            return action
        result = self.client.create_issue(title, body, labels)
        action["result"] = result
        return action

    def assign(self, target_kind: str, target_number: Optional[int], users: Iterable[str]) -> Dict[str, Any]:
        users = [user for user in users if user]
        action = {
            "action_type": "assign",
            "target_kind": target_kind,
            "target_number": target_number,
            "users": users,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run or not target_number or not users:
            return action
        result = self.client.issue_assignees_add(target_number, users)
        action["result"] = result
        return action

    def request_review(self, target_number: Optional[int], reviewers: Iterable[str]) -> Dict[str, Any]:
        reviewers = [reviewer for reviewer in reviewers if reviewer]
        action = {
            "action_type": "review_request",
            "target_kind": "pull_request",
            "target_number": target_number,
            "reviewers": reviewers,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run or not target_number or not reviewers:
            return action
        result = self.client.pull_request_reviewers_request(target_number, reviewers)
        action["result"] = result
        return action

    def set_state(self, target_kind: str, target_number: Optional[int], state: str) -> Dict[str, Any]:
        action = {
            "action_type": "state",
            "target_kind": target_kind,
            "target_number": target_number,
            "state": state,
            "dry_run": self.dry_run,
        }
        self._record(action)
        if self.dry_run or not target_number or not state:
            return action
        if target_kind == "pull_request":
            result = self.client.pull_request_state_update(target_number, state)
        else:
            result = self.client.issue_state_update(target_number, state)
        action["result"] = result
        return action
