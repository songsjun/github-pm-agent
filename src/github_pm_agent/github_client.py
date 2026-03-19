from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, Iterable, List, Optional


class GitHubClient:
    def __init__(self, gh_path: str, repo: str) -> None:
        self.gh_path = gh_path
        self.repo = repo

    def _run(self, args: List[str]) -> str:
        command = [self.gh_path] + args
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def api(self, path: str, params: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        args = ["api", path, "--method", method]
        params = params or {}
        for key, value in params.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if isinstance(item, bool):
                    item = "true" if item else "false"
                args.extend(["-F", f"{key}={item}"])
        output = self._run(args)
        if not output:
            return {}
        return json.loads(output)

    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Any:
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in (variables or {}).items():
            args.extend(["-F", f"{key}={value}"])
        output = self._run(args)
        if not output:
            return {}
        return json.loads(output)

    def issue_comment(self, number: int, body: str) -> Dict[str, Any]:
        return self.api(f"repos/{self.repo}/issues/{number}/comments", {"body": body}, method="POST")

    def issue_labels_add(self, number: int, labels: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/issues/{number}/labels",
            {"labels[]": list(labels)},
            method="POST",
        )

    def issue_labels_remove(self, number: int, labels: Iterable[str]) -> None:
        for label in labels:
            self.api(f"repos/{self.repo}/issues/{number}/labels/{label}", method="DELETE")

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            params["labels[]"] = list(labels)
        return self.api(f"repos/{self.repo}/issues", params, method="POST")

    def add_discussion_comment(self, discussion_id: str, body: str) -> Dict[str, Any]:
        mutation = """
        mutation($discussionId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
            comment { id url }
          }
        }
        """
        return self.graphql(mutation, {"discussionId": discussion_id, "body": body})
