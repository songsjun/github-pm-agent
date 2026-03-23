from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from github_pm_agent.models import Event
from github_pm_agent.utils import parse_iso8601, utc_now_iso


MENTION_RE = re.compile(r"@[A-Za-z0-9_-]+")


def _event_id(prefix: str, raw_id: Any, occurred_at: str, extra: str = "") -> str:
    base = f"{prefix}:{raw_id}:{occurred_at}:{extra}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _first_timestamp(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and "T" in value:
            return value
    return utc_now_iso()


class GitHubPoller:
    _WORKFLOW_RUN_PAGE_LIMIT = 5

    def __init__(self, client: Any, repo: str, default_branch: str, mentions: List[str]) -> None:
        self.client = client
        self.repo = repo
        self.default_branch = default_branch
        self.mentions = set(mentions)

    def _pages(self, path: str, params: Optional[Dict[str, Any]] = None, *, list_key: Optional[str] = None, per_page: int = 100):
        try:
            pages = self.client.iter_api_pages(path, params or {}, list_key=list_key, per_page=per_page)
        except TypeError:
            return []
        try:
            iter(pages)
        except TypeError:
            return []
        return pages

    def _nodes(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        *,
        connection_path,
        cursor_variable,
        page_size_variable,
        page_size,
        reverse: bool = False,
    ):
        try:
            nodes = self.client.iter_graphql_nodes(
                query,
                variables or {},
                connection_path=connection_path,
                cursor_variable=cursor_variable,
                page_size_variable=page_size_variable,
                page_size=page_size,
                reverse=reverse,
            )
        except TypeError:
            return []
        try:
            iter(nodes)
        except TypeError:
            return []
        return nodes

    def poll(self, since_iso: str) -> List[Event]:
        events: List[Event] = []
        events.extend(self._poll_notifications(since_iso))
        events.extend(self._poll_repo_events(since_iso))
        try:
            events.extend(self._poll_projects(since_iso))
        except subprocess.CalledProcessError as exc:
            if not self._is_project_scope_error(exc):
                raise
        events.extend(self._poll_milestones(since_iso))
        events.extend(self._poll_issues(since_iso))
        events.extend(self._poll_issue_events(since_iso))
        events.extend(self._poll_issue_comments(since_iso))
        events.extend(self._poll_commits(since_iso))
        events.extend(self._poll_commit_signals(since_iso))
        events.extend(self._poll_deployments(since_iso))
        events.extend(self._poll_releases(since_iso))
        events.extend(self._poll_workflow_runs(since_iso))
        events.extend(self._poll_pull_request_reviews(since_iso))
        events.extend(self._poll_pull_review_comments(since_iso))
        events.extend(self._poll_discussions(since_iso))
        unique_events: Dict[str, Event] = {}
        for event in events:
            if event.event_id not in unique_events:
                unique_events[event.event_id] = event
        return sorted(
            unique_events.values(),
            key=lambda item: (parse_iso8601(item.occurred_at), item.event_id),
        )

    @staticmethod
    def _is_project_scope_error(exc: subprocess.CalledProcessError) -> bool:
        stderr = " ".join(
            part.strip()
            for part in (exc.stderr, exc.output)
            if isinstance(part, str) and part.strip()
        ).lower()
        if not stderr:
            return False
        if any(
            token in stderr
            for token in ("insufficient_scopes", "insufficient scopes", "missing required scope", "read:project")
        ):
            return True
        return "scope" in stderr and "project" in stderr

    def _poll_notifications(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/notifications",
            {"since": since_iso, "all": True, "participating": False, "per_page": 100},
        ):
            for item in items:
                occurred_at = _first_timestamp(item.get("updated_at"), item.get("last_read_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                reason = (item.get("reason") or "").lower()
                if reason != "mention":
                    continue
                subject = item.get("subject") or {}
                target_kind, target_number = self._notification_target(subject)
                events.append(
                    Event(
                        event_id=_event_id("notification_mention", item.get("id") or subject.get("url", ""), occurred_at, reason),
                        event_type="mention",
                        source="notifications",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor="github",
                        url=subject.get("latest_comment_url") or subject.get("url") or "",
                        title=subject.get("title") or "mention",
                        body=subject.get("title") or "notification mention",
                        target_kind=target_kind,
                        target_number=target_number,
                        metadata={
                            "reason": reason,
                            "notification_id": item.get("id"),
                            "subject_type": subject.get("type"),
                            "unread": item.get("unread", False),
                        },
                    )
                )
        return events

    def _poll_repo_events(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(f"repos/{self.repo}/events", {"per_page": 100}):
            recent_in_page = False
            for item in items:
                occurred_at = _first_timestamp(item.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                recent_in_page = True
                event_type, target_kind, body, metadata = self._repo_event_to_signal(item)
                if not event_type:
                    continue
                events.append(
                    Event(
                        event_id=_event_id("repo_event", item["id"], occurred_at, event_type),
                        event_type=event_type,
                        source="repo_events",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=(item.get("actor") or {}).get("login", ""),
                        url=item.get("repo", {}).get("html_url", ""),
                        title=item.get("type", event_type),
                        body=body,
                        target_kind=target_kind,
                        target_number=None,
                        metadata=metadata,
                    )
                )
            if not recent_in_page:
                break
        return events

    def _poll_issues(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/issues",
            {"state": "all", "since": since_iso},
        ):
            for item in items:
                event_type = "pull_request_changed" if item.get("pull_request") else "issue_changed"
                body = item.get("body") or ""
                occurred_at = _first_timestamp(item.get("updated_at"), item.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                target_kind = "pull_request" if item.get("pull_request") else "issue"
                created_at = _first_timestamp(item.get("created_at"))
                action = "opened" if self._is_newer_than(created_at, since_dt) else "edited"
                event = Event(
                    event_id=_event_id(event_type, item["id"], occurred_at),
                    event_type=event_type,
                    source="issues",
                    occurred_at=occurred_at,
                    repo=self.repo,
                    actor=(item.get("user") or {}).get("login", ""),
                    url=item.get("html_url", ""),
                    title=item.get("title", ""),
                    body=body,
                    target_kind=target_kind,
                    target_number=item.get("number"),
                    metadata={
                        "action": action,
                        "state": item.get("state"),
                        "state_reason": item.get("state_reason"),
                        "labels": [(label or {}).get("name") for label in item.get("labels", [])],
                        "draft": item.get("draft", False),
                        "author": (item.get("user") or {}).get("login", ""),
                        "requested_reviewers": [(reviewer or {}).get("login") for reviewer in item.get("requested_reviewers", [])],
                        "milestone": (item.get("milestone") or {}).get("title"),
                    },
                )
                events.append(event)
                events.extend(self._mention_events(event, body))
        return events

    def _poll_issue_comments(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/issues/comments",
            {"since": since_iso},
        ):
            for item in items:
                occurred_at = _first_timestamp(item.get("updated_at"), item.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                issue_url = item.get("issue_url", "")
                number = int(issue_url.rstrip("/").split("/")[-1]) if issue_url else None
                event = Event(
                    event_id=_event_id("issue_comment", item["id"], occurred_at),
                    event_type="issue_comment",
                    source="issue_comments",
                    occurred_at=occurred_at,
                    repo=self.repo,
                    actor=(item.get("user") or {}).get("login", ""),
                    url=item.get("html_url", ""),
                    title=f"Issue comment on #{number}",
                    body=item.get("body") or "",
                    target_kind="issue",
                    target_number=number,
                    metadata={},
                )
                events.append(event)
                events.extend(self._mention_events(event, event.body))
        return events

    def _poll_issue_events(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(f"repos/{self.repo}/issues/events", {}):
            recent_in_page = False
            for item in items:
                occurred_at = _first_timestamp(item.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                recent_in_page = True
                issue = item.get("issue") or {}
                number = issue.get("number")
                body = item.get("event", "")
                events.append(
                    Event(
                        event_id=_event_id("issue_event", item["id"], occurred_at, item.get("event", "")),
                        event_type=f"issue_event_{item.get('event', 'changed')}",
                        source="issue_events",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=(item.get("actor") or {}).get("login", ""),
                        url=(issue or {}).get("html_url", ""),
                        title=f"Issue event on #{number}",
                        body=body,
                        target_kind="issue",
                        target_number=number,
                        metadata={
                            "event": item.get("event"),
                            "commit_id": item.get("commit_id"),
                            "label": ((item.get("label") or {}).get("name")),
                            "assignee": ((item.get("assignee") or {}).get("login")),
                            "review_requested_reviewer": ((item.get("requested_reviewer") or {}).get("login")),
                            "milestone": ((item.get("milestone") or {}).get("title")),
                            "state_reason": item.get("state_reason"),
                        },
                    )
                )
            if not recent_in_page:
                break
        return events

    def _poll_commits(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/commits",
            {"sha": self.default_branch, "since": since_iso},
        ):
            for item in items:
                commit = item.get("commit") or {}
                author = (item.get("author") or {}).get("login") or ((commit.get("author") or {}).get("name") or "")
                occurred_at = _first_timestamp((commit.get("author") or {}).get("date"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                body = commit.get("message") or ""
                events.append(
                    Event(
                        event_id=_event_id("commit", item["sha"], occurred_at),
                        event_type="commit",
                        source="commits",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=author,
                        url=item.get("html_url", ""),
                        title=body.splitlines()[0] if body else item["sha"][:7],
                        body=body,
                        target_kind="commit",
                        target_number=None,
                        metadata={"sha": item["sha"]},
                    )
                )
        return events

    def _poll_projects(self, since_iso: str) -> List[Event]:
        owner, name = self.repo.split("/", 1)
        since_dt = parse_iso8601(since_iso)
        query = """
        query($owner: String!, $name: String!, $after: String, $first: Int!) {
          repository(owner: $owner, name: $name) {
            projectsV2(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                id
                number
                title
                shortDescription
                updatedAt
                closed
                url
              }
            }
          }
        }
        """
        events: List[Event] = []
        for node in self._nodes(
            query,
            {"owner": owner, "name": name},
            connection_path=("data", "repository", "projectsV2"),
            cursor_variable="after",
            page_size_variable="first",
            page_size=20,
        ):
            occurred_at = _first_timestamp(node.get("updatedAt"))
            if not self._is_newer_than(occurred_at, since_dt):
                break
            events.append(
                Event(
                    event_id=_event_id("project", node.get("id") or node.get("number"), occurred_at),
                    event_type="project_changed",
                    source="projects_v2",
                    occurred_at=occurred_at,
                    repo=self.repo,
                    actor="github",
                    url=node.get("url", ""),
                    title=node.get("title", ""),
                    body=node.get("shortDescription") or "",
                    target_kind="project",
                    target_number=node.get("number"),
                    metadata={
                        "project_id": node.get("id"),
                        "closed": node.get("closed", False),
                    },
                )
            )
        return events

    def _poll_milestones(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/milestones",
            {"state": "all", "sort": "due_on", "direction": "desc", "per_page": 100},
        ):
            recent_in_page = False
            for item in items:
                occurred_at = _first_timestamp(item.get("updated_at"), item.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                recent_in_page = True
                events.append(
                    Event(
                        event_id=_event_id("milestone", item.get("id") or item.get("number"), occurred_at),
                        event_type="milestone_changed",
                        source="milestones",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=(item.get("creator") or {}).get("login", ""),
                        url=item.get("html_url", ""),
                        title=item.get("title", ""),
                        body=item.get("description") or "",
                        target_kind="milestone",
                        target_number=item.get("number"),
                        metadata={
                            "milestone_id": item.get("id"),
                            "state": item.get("state"),
                            "open_issues": item.get("open_issues"),
                            "closed_issues": item.get("closed_issues"),
                            "due_on": item.get("due_on"),
                        },
                    )
                )
            if not recent_in_page:
                break
        return events

    def _poll_commit_signals(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/commits",
            {"sha": self.default_branch, "since": since_iso},
        ):
            for item in items:
                commit = item.get("commit") or {}
                sha = item.get("sha")
                occurred_at = _first_timestamp((commit.get("author") or {}).get("date"))
                if not sha or not self._is_newer_than(occurred_at, since_dt):
                    continue
                status = self.client.api(f"repos/{self.repo}/commits/{sha}/status")
                state = (status or {}).get("state")
                if state and state != "success":
                    events.append(
                        Event(
                            event_id=_event_id("commit_status", sha, occurred_at, state),
                            event_type="commit_status_failed" if state in {"failure", "error"} else "commit_status_pending",
                            source="commit_statuses",
                            occurred_at=occurred_at,
                            repo=self.repo,
                            actor=(item.get("author") or {}).get("login", ""),
                            url=item.get("html_url", ""),
                            title=commit.get("message", sha)[:80],
                            body=f"status={state}",
                            target_kind="commit",
                            target_number=None,
                            metadata={
                                "sha": sha,
                                "state": state,
                                "context": (status or {}).get("context") or ((status or {}).get("statuses") or [{}])[0].get("context", ""),
                            },
                        )
                    )
                checks = self.client.api(f"repos/{self.repo}/commits/{sha}/check-runs")
                for check_run in (checks or {}).get("check_runs", []):
                    conclusion = check_run.get("conclusion")
                    check_status = check_run.get("status")
                    if conclusion in {"success", "neutral", "skipped"} and check_status == "completed":
                        continue
                    events.append(
                        Event(
                            event_id=_event_id("check_run", check_run.get("id") or sha, occurred_at, check_run.get("name", "")),
                            event_type="check_run_failed" if conclusion in {"failure", "cancelled", "timed_out", "action_required"} else "check_run_pending",
                            source="check_runs",
                            occurred_at=occurred_at,
                            repo=self.repo,
                            actor=(check_run.get("app") or {}).get("slug", ""),
                            url=check_run.get("html_url", item.get("html_url", "")),
                            title=check_run.get("name", "check run"),
                            body=f"status={check_status} conclusion={conclusion}",
                            target_kind="commit",
                            target_number=None,
                            metadata={
                                "sha": sha,
                                "name": check_run.get("name"),
                                "status": check_status,
                                "conclusion": conclusion,
                            },
                        )
                    )
        return events

    def _poll_deployments(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(f"repos/{self.repo}/deployments", {"per_page": 50}):
            for item in items:
                occurred_at = _first_timestamp(item.get("created_at"), item.get("updated_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                deployment_id = item.get("id")
                latest_status: Dict[str, Any] = {}
                if deployment_id is not None:
                    statuses = self.client.api(f"repos/{self.repo}/deployments/{deployment_id}/statuses")
                    if isinstance(statuses, list) and statuses:
                        latest_status = statuses[0]
                state = latest_status.get("state") or item.get("state")
                events.append(
                    Event(
                        event_id=_event_id("deployment", deployment_id or item.get("sha", ""), occurred_at, state or ""),
                        event_type="deployment_failed" if state in {"failure", "error"} else "deployment_status",
                        source="deployments",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=(item.get("creator") or {}).get("login", ""),
                        url=item.get("html_url", ""),
                        title=item.get("task", "deployment"),
                        body=f"state={state}",
                        target_kind="deployment",
                        target_number=deployment_id,
                        metadata={
                            "state": state,
                            "environment": (item.get("environment") or {}).get("name") if isinstance(item.get("environment"), dict) else item.get("environment"),
                            "ref": item.get("ref"),
                            "sha": item.get("sha"),
                        },
                    )
                )
        return events

    def _poll_releases(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(f"repos/{self.repo}/releases", {"per_page": 50}):
            for item in items:
                occurred_at = _first_timestamp(item.get("published_at"), item.get("created_at"), item.get("updated_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                tag = item.get("tag_name") or item.get("name") or "release"
                events.append(
                    Event(
                        event_id=_event_id("release", item["id"], occurred_at, tag),
                        event_type="release_published" if not item.get("draft") else "release_draft",
                        source="releases",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=(item.get("author") or {}).get("login", ""),
                        url=item.get("html_url", ""),
                        title=item.get("name") or tag,
                        body=item.get("body") or "",
                        target_kind="release",
                        target_number=item.get("id"),
                        metadata={
                            "tag_name": tag,
                            "draft": item.get("draft", False),
                            "prerelease": item.get("prerelease", False),
                            "published_at": item.get("published_at"),
                        },
                    )
                )
        return events

    def _poll_workflow_runs(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        # The Actions runs list does not expose an updated-at cursor, so poll a bounded recent window.
        for page_index, runs in enumerate(
            self._pages(
                f"repos/{self.repo}/actions/runs",
                {},
                list_key="workflow_runs",
            ),
            start=1,
        ):
            for run in runs:
                occurred_at = _first_timestamp(run.get("updated_at"), run.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                event_type = "workflow_failed" if run.get("conclusion") == "failure" else "workflow_run"
                events.append(
                    Event(
                        event_id=_event_id("workflow", run["id"], occurred_at, run.get("conclusion", "")),
                        event_type=event_type,
                        source="workflow_runs",
                        occurred_at=occurred_at,
                        repo=self.repo,
                        actor=(run.get("actor") or {}).get("login", ""),
                        url=run.get("html_url", ""),
                        title=run.get("name", "workflow"),
                        body=f"status={run.get('status')} conclusion={run.get('conclusion')}",
                        target_kind="workflow_run",
                        target_number=run.get("run_number"),
                        metadata={"status": run.get("status"), "conclusion": run.get("conclusion")},
                    )
                )
            if page_index >= self._WORKFLOW_RUN_PAGE_LIMIT:
                break
        return events

    def _poll_pull_request_reviews(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for pulls in self._pages(
            f"repos/{self.repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 50},
        ):
            recent_pulls: List[Dict[str, Any]] = []
            for pr in pulls:
                pr_updated = _first_timestamp(pr.get("updated_at"), pr.get("created_at"))
                if self._is_newer_than(pr_updated, since_dt):
                    recent_pulls.append(pr)
            if not recent_pulls:
                break
            for pr in recent_pulls:
                pr_updated = _first_timestamp(pr.get("updated_at"), pr.get("created_at"))
                for reviews in self._pages(
                    f"repos/{self.repo}/pulls/{pr['number']}/reviews",
                    {"per_page": 100},
                ):
                    for review in reviews:
                        occurred_at = _first_timestamp(review.get("submitted_at"), review.get("created_at"), pr_updated)
                        if not self._is_newer_than(occurred_at, since_dt):
                            continue
                        body = review.get("body") or review.get("state", "")
                        event = Event(
                            event_id=_event_id("pull_request_review", review["id"], occurred_at),
                            event_type="pull_request_review",
                            source="pull_reviews",
                            occurred_at=occurred_at,
                            repo=self.repo,
                            actor=(review.get("user") or {}).get("login", ""),
                            url=review.get("html_url") or pr.get("html_url", ""),
                            title=f"PR review on #{pr['number']}",
                            body=body,
                            target_kind="pull_request",
                            target_number=pr["number"],
                            metadata={"state": review.get("state")},
                        )
                        events.append(event)
                        events.extend(self._mention_events(event, body))
        return events

    def _poll_pull_review_comments(self, since_iso: str) -> List[Event]:
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for items in self._pages(
            f"repos/{self.repo}/pulls/comments",
            {"since": since_iso},
        ):
            for item in items:
                occurred_at = _first_timestamp(item.get("updated_at"), item.get("created_at"))
                if not self._is_newer_than(occurred_at, since_dt):
                    continue
                pr_url = item.get("pull_request_url", "")
                number = int(pr_url.rstrip("/").split("/")[-1]) if pr_url else None
                event = Event(
                    event_id=_event_id("pull_request_review_comment", item["id"], occurred_at),
                    event_type="pull_request_review_comment",
                    source="pull_review_comments",
                    occurred_at=occurred_at,
                    repo=self.repo,
                    actor=(item.get("user") or {}).get("login", ""),
                    url=item.get("html_url", ""),
                    title=f"PR review comment on #{number}",
                    body=item.get("body") or "",
                    target_kind="pull_request",
                    target_number=number,
                    metadata={"path": item.get("path")},
                )
                events.append(event)
                events.extend(self._mention_events(event, event.body))
        return events

    def _poll_discussions(self, since_iso: str) -> List[Event]:
        owner, name = self.repo.split("/", 1)
        since_dt = parse_iso8601(since_iso)
        query = """
        query($owner: String!, $name: String!, $after: String, $first: Int!) {
          repository(owner: $owner, name: $name) {
            discussions(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                id
                number
                title
                body
                url
                createdAt
                updatedAt
                author { login }
              }
            }
          }
        }
        """
        events: List[Event] = []
        for node in self._nodes(
            query,
            {"owner": owner, "name": name},
            connection_path=("data", "repository", "discussions"),
            cursor_variable="after",
            page_size_variable="first",
            page_size=25,
        ):
            updated_at = _first_timestamp(node.get("updatedAt"), node.get("createdAt"))
            if not self._is_newer_than(updated_at, since_dt):
                break
            event = Event(
                event_id=_event_id("discussion", node["id"], updated_at),
                event_type="discussion",
                source="discussions",
                occurred_at=updated_at,
                repo=self.repo,
                actor=(node.get("author") or {}).get("login", ""),
                url=node.get("url", ""),
                title=node.get("title", ""),
                body=node.get("body") or "",
                target_kind="discussion",
                target_number=node.get("number"),
                metadata={"node_id": node.get("id")},
            )
            events.append(event)
            events.extend(self._mention_events(event, event.body))
            events.extend(self._poll_discussion_comments(node, since_dt))
        return events

    def _poll_discussion_comments(self, node: Dict[str, Any], since_dt: datetime) -> List[Event]:
        discussion_id = node.get("id")
        if not discussion_id:
            return []
        query = """
        query($discussionId: ID!, $before: String, $last: Int!) {
          node(id: $discussionId) {
            ... on Discussion {
              comments(last: $last, before: $before) {
                pageInfo {
                  hasPreviousPage
                  startCursor
                }
                nodes {
                  id
                  body
                  createdAt
                  updatedAt
                  author { login }
                  url
                }
              }
            }
          }
        }
        """
        events: List[Event] = []
        for comment in self._nodes(
            query,
            {"discussionId": discussion_id},
            connection_path=("data", "node", "comments"),
            cursor_variable="before",
            page_size_variable="last",
            page_size=50,
            reverse=True,
        ):
            comment_updated = _first_timestamp(comment.get("updatedAt"), comment.get("createdAt"))
            if not self._is_newer_than(comment_updated, since_dt):
                continue
            comment_event = Event(
                event_id=_event_id("discussion_comment", comment["id"], comment_updated),
                event_type="discussion_comment",
                source="discussion_comments",
                occurred_at=comment_updated,
                repo=self.repo,
                actor=(comment.get("author") or {}).get("login", ""),
                url=comment.get("url", ""),
                title=f"Discussion comment on #{node.get('number')}",
                body=comment.get("body") or "",
                target_kind="discussion",
                target_number=node.get("number"),
                metadata={"discussion_node_id": discussion_id},
            )
            events.append(comment_event)
            events.extend(self._mention_events(comment_event, comment_event.body))
        return events

    def _repo_event_to_signal(self, item: Dict[str, Any]) -> tuple[str, str, str, Dict[str, Any]]:
        payload = item.get("payload") or {}
        event_type = item.get("type")
        if event_type == "PushEvent":
            forced = bool(payload.get("forced"))
            ref = payload.get("ref") or ""
            branch = ref.split("/")[-1] if ref else ""
            commits = payload.get("commits") or []
            message = (commits[0].get("message") if commits else "") or ""
            return (
                "force_push" if forced else "push",
                "branch",
                message,
                {
                    "ref": ref,
                    "branch": branch,
                    "forced": forced,
                    "size": payload.get("size"),
                    "before": payload.get("before"),
                    "head": payload.get("head"),
                },
            )
        if event_type == "CreateEvent" and payload.get("ref_type") == "branch":
            ref = payload.get("ref") or ""
            return (
                "branch_ref_created",
                "branch",
                f"branch created: {ref}",
                {
                    "ref": ref,
                    "ref_type": payload.get("ref_type"),
                },
            )
        if event_type == "DeleteEvent" and payload.get("ref_type") == "branch":
            ref = payload.get("ref") or ""
            return (
                "branch_ref_deleted",
                "branch",
                f"branch deleted: {ref}",
                {
                    "ref": ref,
                    "ref_type": payload.get("ref_type"),
                },
            )
        if event_type == "ReleaseEvent":
            release = payload.get("release") or {}
            return (
                "release_published",
                "release",
                release.get("name") or release.get("tag_name") or "release published",
                {
                    "tag_name": release.get("tag_name"),
                    "draft": release.get("draft", False),
                    "prerelease": release.get("prerelease", False),
                },
            )
        return "", "", "", {}

    @staticmethod
    def _is_newer_than(occurred_at: str, since_dt: datetime) -> bool:
        return parse_iso8601(occurred_at) > since_dt

    def _mention_events(self, event: Event, body: str) -> Iterable[Event]:
        mentions = set(MENTION_RE.findall(f"{event.title}\n{body or ''}"))
        watched = sorted(mentions.intersection(self.mentions))
        if not watched:
            return []
        return [
            Event(
                event_id=_event_id("mention", event.event_id, event.occurred_at, mention),
                event_type="mention",
                source=event.source,
                occurred_at=event.occurred_at,
                repo=event.repo,
                actor=event.actor,
                url=event.url,
                title=event.title,
                body=body,
                target_kind=event.target_kind,
                target_number=event.target_number,
                metadata={"mention": mention, "related_event_id": event.event_id, **event.metadata},
            )
            for mention in watched
        ]

    def _notification_target(self, subject: Dict[str, Any]) -> tuple[str, Optional[int]]:
        subject_type = (subject.get("type") or "").lower()
        url = subject.get("url") or ""
        mapping = {
            "issue": "issue",
            "pullrequest": "pull_request",
            "discussion": "discussion",
            "commit": "commit",
            "release": "release",
        }
        target_kind = mapping.get(subject_type, "issue")
        number: Optional[int] = None
        if url and target_kind in {"issue", "pull_request", "discussion", "release"}:
            try:
                number = int(url.rstrip("/").split("/")[-1])
            except (TypeError, ValueError):
                number = None
        return target_kind, number
