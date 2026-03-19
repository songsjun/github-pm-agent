from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List

from github_pm_agent.models import Event
from github_pm_agent.utils import parse_iso8601, utc_now_iso


MENTION_RE = re.compile(r"@[A-Za-z0-9_-]+")


def _event_id(prefix: str, raw_id: Any, occurred_at: str, extra: str = "") -> str:
    base = f"{prefix}:{raw_id}:{occurred_at}:{extra}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


class GitHubPoller:
    def __init__(self, client: Any, repo: str, default_branch: str, mentions: List[str]) -> None:
        self.client = client
        self.repo = repo
        self.default_branch = default_branch
        self.mentions = set(mentions)

    def poll(self, since_iso: str) -> List[Event]:
        events: List[Event] = []
        events.extend(self._poll_issues(since_iso))
        events.extend(self._poll_issue_events(since_iso))
        events.extend(self._poll_issue_comments(since_iso))
        events.extend(self._poll_commits(since_iso))
        events.extend(self._poll_workflow_runs(since_iso))
        events.extend(self._poll_pull_request_reviews(since_iso))
        events.extend(self._poll_pull_review_comments(since_iso))
        events.extend(self._poll_discussions(since_iso))
        return sorted(events, key=lambda item: parse_iso8601(item.occurred_at))

    def _poll_issues(self, since_iso: str) -> List[Event]:
        items = self.client.api(
            f"repos/{self.repo}/issues",
            {"state": "all", "since": since_iso, "per_page": 100},
        )
        events: List[Event] = []
        for item in items:
            event_type = "pull_request_changed" if item.get("pull_request") else "issue_changed"
            body = item.get("body") or ""
            occurred_at = item.get("updated_at") or item.get("created_at") or utc_now_iso()
            target_kind = "pull_request" if item.get("pull_request") else "issue"
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
                metadata={"state": item.get("state"), "labels": [(label or {}).get("name") for label in item.get("labels", [])]},
            )
            events.append(event)
            events.extend(self._mention_events(event, body))
        return events

    def _poll_issue_comments(self, since_iso: str) -> List[Event]:
        items = self.client.api(
            f"repos/{self.repo}/issues/comments",
            {"since": since_iso, "per_page": 100},
        )
        events: List[Event] = []
        for item in items:
            occurred_at = item.get("updated_at") or item.get("created_at") or utc_now_iso()
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
        items = self.client.api(
            f"repos/{self.repo}/issues/events",
            {"per_page": 100},
        )
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for item in items:
            occurred_at = item.get("created_at") or utc_now_iso()
            if parse_iso8601(occurred_at) < since_dt:
                continue
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
                    metadata={"event": item.get("event"), "commit_id": item.get("commit_id")},
                )
            )
        return events

    def _poll_commits(self, since_iso: str) -> List[Event]:
        items = self.client.api(
            f"repos/{self.repo}/commits",
            {"sha": self.default_branch, "since": since_iso, "per_page": 100},
        )
        events: List[Event] = []
        for item in items:
            commit = item.get("commit") or {}
            author = (item.get("author") or {}).get("login") or ((commit.get("author") or {}).get("name") or "")
            occurred_at = ((commit.get("author") or {}).get("date")) or utc_now_iso()
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

    def _poll_workflow_runs(self, since_iso: str) -> List[Event]:
        response = self.client.api(f"repos/{self.repo}/actions/runs", {"per_page": 50})
        runs = response.get("workflow_runs", [])
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for run in runs:
            occurred_at = run.get("updated_at") or run.get("created_at") or utc_now_iso()
            if parse_iso8601(occurred_at) < since_dt:
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
        return events

    def _poll_pull_request_reviews(self, since_iso: str) -> List[Event]:
        items = self.client.api(
            f"repos/{self.repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 50},
        )
        since_dt = parse_iso8601(since_iso)
        events: List[Event] = []
        for pr in items:
            pr_updated = pr.get("updated_at") or pr.get("created_at") or utc_now_iso()
            if parse_iso8601(pr_updated) < since_dt:
                continue
            reviews = self.client.api(f"repos/{self.repo}/pulls/{pr['number']}/reviews", {"per_page": 50})
            for review in reviews:
                occurred_at = review.get("submitted_at") or review.get("body") or pr_updated
                if isinstance(occurred_at, str) and "T" not in occurred_at:
                    occurred_at = pr_updated
                if parse_iso8601(occurred_at) < since_dt:
                    continue
                body = review.get("body") or review.get("state", "")
                event = Event(
                    event_id=_event_id("pull_request_review", review["id"], occurred_at),
                    event_type="pull_request_review",
                    source="pull_reviews",
                    occurred_at=occurred_at,
                    repo=self.repo,
                    actor=(review.get("user") or {}).get("login", ""),
                    url=pr.get("html_url", ""),
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
        items = self.client.api(
            f"repos/{self.repo}/pulls/comments",
            {"since": since_iso, "per_page": 100},
        )
        events: List[Event] = []
        for item in items:
            occurred_at = item.get("updated_at") or item.get("created_at") or utc_now_iso()
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
        query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            discussions(first: 25, orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                id
                number
                title
                body
                url
                createdAt
                updatedAt
                author { login }
                comments(first: 20) {
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
        }
        """
        response = self.client.graphql(query, {"owner": owner, "name": name})
        nodes = (((response.get("data") or {}).get("repository") or {}).get("discussions") or {}).get("nodes", [])
        events: List[Event] = []
        since_dt = parse_iso8601(since_iso)
        for node in nodes:
            updated_at = node.get("updatedAt") or node.get("createdAt") or utc_now_iso()
            if parse_iso8601(updated_at) >= since_dt:
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
            for comment in ((node.get("comments") or {}).get("nodes") or []):
                comment_updated = comment.get("updatedAt") or comment.get("createdAt") or utc_now_iso()
                if parse_iso8601(comment_updated) < since_dt:
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
                    metadata={"discussion_node_id": node.get("id")},
                )
                events.append(comment_event)
                events.extend(self._mention_events(comment_event, comment_event.body))
        return events

    def _mention_events(self, event: Event, body: str) -> Iterable[Event]:
        mentions = set(MENTION_RE.findall(body or ""))
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
