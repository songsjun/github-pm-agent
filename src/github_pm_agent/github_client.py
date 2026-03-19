from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence


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

    def iter_api_pages(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        method: str = "GET",
        list_key: Optional[str] = None,
        per_page: int = 100,
    ) -> Iterator[List[Dict[str, Any]]]:
        page = 1
        base_params = dict(params or {})
        page_size = int(base_params.get("per_page", per_page))
        while True:
            page_params = dict(base_params)
            page_params["per_page"] = page_size
            page_params["page"] = page
            payload = self.api(path, page_params, method=method)
            if list_key is None:
                items = payload if isinstance(payload, list) else []
            else:
                items = (payload or {}).get(list_key, [])
            if not items:
                return
            yield items
            if len(items) < page_size:
                return
            page += 1

    def iter_graphql_nodes(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        *,
        connection_path: Sequence[str],
        cursor_variable: str,
        page_size_variable: str,
        page_size: int,
        reverse: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        base_variables = dict(variables or {})
        while True:
            page_variables = dict(base_variables)
            page_variables[page_size_variable] = page_size
            if cursor is not None:
                page_variables[cursor_variable] = cursor
            payload = self.graphql(query, page_variables)
            connection = self._connection_at_path(payload, connection_path)
            nodes = connection.get("nodes", [])
            if not nodes:
                return
            for node in nodes:
                yield node
            page_info = connection.get("pageInfo") or {}
            if reverse:
                has_more = bool(page_info.get("hasPreviousPage"))
                cursor = page_info.get("startCursor")
            else:
                has_more = bool(page_info.get("hasNextPage"))
                cursor = page_info.get("endCursor")
            if not has_more or not cursor:
                return

    @staticmethod
    def _connection_at_path(payload: Any, path: Sequence[str]) -> Dict[str, Any]:
        current = payload
        for segment in path:
            if not isinstance(current, dict):
                return {}
            current = current.get(segment)
        return current if isinstance(current, dict) else {}

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

    def issue_assignees_add(self, number: int, assignees: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/issues/{number}/assignees",
            {"assignees[]": list(assignees)},
            method="POST",
        )

    def pull_request_reviewers_request(self, number: int, reviewers: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/pulls/{number}/requested_reviewers",
            {"reviewers[]": list(reviewers)},
            method="POST",
        )

    def issue_state_update(self, number: int, state: str) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/issues/{number}",
            {"state": state},
            method="PATCH",
        )

    def pull_request_state_update(self, number: int, state: str) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/pulls/{number}",
            {"state": state},
            method="PATCH",
        )

    def add_discussion_comment(self, discussion_id: str, body: str) -> Dict[str, Any]:
        mutation = """
        mutation($discussionId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
            comment { id url }
          }
        }
        """
        return self.graphql(mutation, {"discussionId": discussion_id, "body": body})
