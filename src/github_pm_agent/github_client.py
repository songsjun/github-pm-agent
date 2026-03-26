from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

logger = logging.getLogger(__name__)


class GitHubClient:
    COMMAND_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        gh_path: str,
        repo: str,
        token_env: Optional[str] = None,
        gh_user: Optional[str] = None,
    ) -> None:
        self.gh_path = gh_path
        self.repo = repo
        self.token_env = token_env
        self.gh_user = gh_user

    def _resolve_token(self) -> Optional[str]:
        """Return an auth token: env var takes priority, then `gh auth token --user`."""
        if self.token_env:
            token = os.environ.get(self.token_env)
            if token:
                return token
            logger.warning(
                "token_env %r is configured but the environment variable is not set; "
                "falling back to gh_user %r — verify the secret is injected correctly.",
                self.token_env,
                self.gh_user,
            )
        if self.gh_user:
            try:
                result = subprocess.run(
                    [self.gh_path, "auth", "token", "--user", self.gh_user],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.COMMAND_TIMEOUT_SECONDS,
                )
                token = result.stdout.strip()
                if token:
                    return token
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
        return None

    def _run(self, args: List[str]) -> str:
        command = [self.gh_path] + args
        token = self._resolve_token()
        kwargs = {
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": self.COMMAND_TIMEOUT_SECONDS,
        }
        if token:
            env = {**os.environ, "GITHUB_TOKEN": token}
            result = subprocess.run(command, env=env, **kwargs)
        else:
            result = subprocess.run(command, **kwargs)
        return result.stdout.strip()

    def api(self, path: str, params: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        args = ["api", path, "--method", method]
        params = params or {}
        for key, value in params.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if isinstance(item, bool):
                    # -F interprets booleans correctly
                    args.extend(["-F", f"{key}={'true' if item else 'false'}"])
                elif isinstance(item, (int, float)):
                    args.extend(["-F", f"{key}={item}"])
                else:
                    # -f treats value as literal string; -F would interpret @prefix as a file path
                    args.extend(["-f", f"{key}={item}"])
        output = self._run(args)
        if not output:
            return {}
        return json.loads(output)

    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Any:
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in (variables or {}).items():
            # Use -f (string literal) for str values to avoid gh treating @-prefixed
            # values as file paths. Use -F (typed) for int/bool.
            if isinstance(value, (int, bool)):
                args.extend(["-F", f"{key}={value}"])
            else:
                args.extend(["-f", f"{key}={value}"])
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

    def issue_update(self, number: int, **fields: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for key in ("title", "body", "milestone", "state", "assignees", "labels"):
            value = fields.get(key)
            if value is None:
                continue
            if key in {"assignees", "labels"}:
                params[f"{key}[]"] = list(value)
            else:
                params[key] = value
        return self.api(f"repos/{self.repo}/issues/{number}", params, method="PATCH")

    def issue_labels_add(self, number: int, labels: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/issues/{number}/labels",
            {"labels[]": list(labels)},
            method="POST",
        )

    def get_pr_diff(self, pr_number: int) -> str:
        """Return the unified diff of a pull request (up to ~100 KB)."""
        try:
            return self._run(
                ["api", f"repos/{self.repo}/pulls/{pr_number}",
                 "--accept", "application/vnd.github.v3.diff"]
            )
        except subprocess.CalledProcessError:
            return ""

    def submit_pr_review(self, pr_number: int, event: str, body: str = "") -> None:
        """Submit a PR review with event=APPROVE | REQUEST_CHANGES | COMMENT."""
        self.api(
            f"repos/{self.repo}/pulls/{pr_number}/reviews",
            {"event": event, "body": body},
            method="POST",
        )

    def issue_labels_remove(self, number: int, labels: Iterable[str]) -> None:
        for label in labels:
            try:
                self.api(f"repos/{self.repo}/issues/{number}/labels/{label}", method="DELETE")
            except subprocess.CalledProcessError as exc:
                # 404 means the label is not present — treat as success
                if b"404" not in (exc.stderr or b"") and "404" not in str(exc):
                    raise

    def issue_assignees_remove(self, number: int, assignees: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/issues/{number}/assignees",
            {"assignees[]": list(assignees)},
            method="DELETE",
        )

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

    def pull_request_reviewers_remove(self, number: int, reviewers: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/pulls/{number}/requested_reviewers",
            {"reviewers[]": list(reviewers)},
            method="DELETE",
        )

    def pull_request_reviewers_request(self, number: int, reviewers: Iterable[str]) -> Dict[str, Any]:
        return self.api(
            f"repos/{self.repo}/pulls/{number}/requested_reviewers",
            {"reviewers[]": list(reviewers)},
            method="POST",
        )

    def pull_request_mark_draft(self, number: int) -> Dict[str, Any]:
        return self.api(f"repos/{self.repo}/pulls/{number}/convert-to-draft", method="POST")

    def pull_request_mark_ready(self, number: int) -> Dict[str, Any]:
        return self.api(f"repos/{self.repo}/pulls/{number}/ready_for_review", method="POST")

    def pull_request_merge(self, number: int, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = dict(params or {})
        return self.api(f"repos/{self.repo}/pulls/{number}/merge", payload, method="PUT")

    def pull_request_review_submit(self, number: int, decision: str, body: str = "", commit_id: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {"event": decision}
        if body:
            payload["body"] = body
        if commit_id:
            payload["commit_id"] = commit_id
        return self.api(f"repos/{self.repo}/pulls/{number}/reviews", payload, method="POST")

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

    def rerun_workflow_run(self, run_id: int) -> Dict[str, Any]:
        return self.api(f"repos/{self.repo}/actions/runs/{run_id}/rerun", method="POST")

    def cancel_workflow_run(self, run_id: int) -> Dict[str, Any]:
        return self.api(f"repos/{self.repo}/actions/runs/{run_id}/cancel", method="POST")

    def create_release(self, **fields: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for key in (
            "tag_name",
            "target_commitish",
            "name",
            "body",
            "draft",
            "prerelease",
            "generate_release_notes",
            "make_latest",
        ):
            value = fields.get(key)
            if value is not None:
                params[key] = value
        return self.api(f"repos/{self.repo}/releases", params, method="POST")

    def get_discussion_comments(self, owner: str, name: str, number: int) -> List[Dict[str, Any]]:
        query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            discussion(number: $number) {
              comments(last: 100) {
                nodes {
                  body
                  createdAt
                  author { login }
                  replies(last: 10) {
                    nodes {
                      body
                      createdAt
                      author { login }
                    }
                  }
                }
              }
            }
          }
        }
        """
        result = self.graphql(query, {"owner": owner, "name": name, "number": number})
        if not isinstance(result, dict):
            return []
        nodes = (
            result.get("data", {})
            .get("repository", {})
            .get("discussion", {})
            .get("comments", {})
            .get("nodes", [])
        )
        if not isinstance(nodes, list):
            return []

        flat_comments: List[Dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            flat_comments.append(
                {
                    "body": node.get("body"),
                    "createdAt": node.get("createdAt"),
                    "author": node.get("author"),
                }
            )
            reply_nodes = (node.get("replies") or {}).get("nodes", [])
            if not isinstance(reply_nodes, list):
                continue
            for reply in reply_nodes:
                if not isinstance(reply, dict):
                    continue
                flat_comments.append(
                    {
                        "body": reply.get("body"),
                        "createdAt": reply.get("createdAt"),
                        "author": reply.get("author"),
                    }
                )

        flat_comments.sort(key=lambda comment: comment.get("createdAt", ""))
        return flat_comments

    def add_discussion_comment(self, discussion_id: str, body: str) -> Dict[str, Any]:
        mutation = """
        mutation($discussionId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
            comment { id url }
          }
        }
        """
        return self.graphql(mutation, {"discussionId": discussion_id, "body": body})

    def create_discussion(self, repository_id: str, category_id: str, title: str, body: str) -> Dict[str, Any]:
        mutation = """
        mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
          createDiscussion(input: {repositoryId: $repositoryId, categoryId: $categoryId, title: $title, body: $body}) {
            discussion { id url title }
          }
        }
        """
        return self.graphql(
            mutation,
            {
                "repositoryId": repository_id,
                "categoryId": category_id,
                "title": title,
                "body": body,
            },
        )

    def update_discussion(
        self,
        discussion_id: str,
        title: str = "",
        body: str = "",
        category_id: str = "",
    ) -> Dict[str, Any]:
        variables: Dict[str, Any] = {"discussionId": discussion_id}
        input_fields = ["discussionId: $discussionId"]
        variable_defs = ["$discussionId: ID!"]
        if title:
            variables["title"] = title
            variable_defs.append("$title: String")
            input_fields.append("title: $title")
        if body:
            variables["body"] = body
            variable_defs.append("$body: String")
            input_fields.append("body: $body")
        if category_id:
            variables["categoryId"] = category_id
            variable_defs.append("$categoryId: ID")
            input_fields.append("categoryId: $categoryId")
        mutation = f"""
        mutation({', '.join(variable_defs)}) {{
          updateDiscussion(input: {{{', '.join(input_fields)}}}) {{
            discussion {{ id url title }}
          }}
        }}
        """
        return self.graphql(mutation, variables)

    def update_project_v2_item_field_value(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
        value: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not value:
            return {}

        field_key, field_type, field_value = self._project_value_payload(value)
        if field_key is None:
            return {}

        mutation = f"""
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $fieldValue: {field_type}!) {{
          updateProjectV2ItemFieldValue(
            input: {{
              projectId: $projectId
              itemId: $itemId
              fieldId: $fieldId
              value: {{{field_key}: $fieldValue}}
            }}
          ) {{
            projectV2Item {{ id }}
          }}
        }}
        """
        return self.graphql(
            mutation,
            {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": field_id,
                "fieldValue": field_value,
            },
        )

    def _project_value_payload(self, value: Dict[str, Any]) -> tuple[Optional[str], str, Any]:
        if "text" in value:
            return "text", "String", value["text"]
        if "number" in value:
            return "number", "Float", value["number"]
        if "date" in value:
            return "date", "Date", value["date"]
        if "single_select_option_id" in value:
            return "singleSelectOptionId", "String", value["single_select_option_id"]
        if "iteration_id" in value:
            return "iterationId", "String", value["iteration_id"]
        if "singleSelectOptionId" in value:
            return "singleSelectOptionId", "String", value["singleSelectOptionId"]
        if "iterationId" in value:
            return "iterationId", "String", value["iterationId"]
        return None, "String", None
