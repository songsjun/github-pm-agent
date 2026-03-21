from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from github_pm_agent.models import Event
from github_pm_agent.utils import parse_iso8601, utc_now_iso


def _synthetic_event_id(kind: str, number: int, updated_at: str) -> str:
    digest = hashlib.sha1(f"{kind}:{number}:{updated_at}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"


class StatusProbe:
    def __init__(self, client: Any, repo: str, config: Dict[str, Any]) -> None:
        self.client = client
        self.repo = repo
        self.config = config

    def scan(self) -> List[Event]:
        events: List[Event] = []
        events.extend(self._stale_pr_review_events())
        events.extend(self._blocked_issue_stale_events())
        events.extend(self._review_churn_events())
        events.extend(self._repeated_ci_instability_events())
        events.extend(self._stale_discussion_decision_events())
        events.extend(self._docs_drift_before_release_events())
        events.extend(self._release_readiness_events())
        return events

    def _stale_pr_review_events(self) -> List[Event]:
        threshold_hours = self.config.get("engine", {}).get("stale_pr_review_hours", 48)
        now = parse_iso8601(utc_now_iso())
        pulls = self.client.api(
            f"repos/{self.repo}/pulls",
            {"state": "open", "sort": "updated", "direction": "asc", "per_page": 100},
        )
        events: List[Event] = []
        for pr in pulls:
            if pr.get("draft"):
                continue
            updated_at = pr.get("updated_at") or pr.get("created_at") or utc_now_iso()
            hours_waiting = (now - parse_iso8601(updated_at)).total_seconds() / 3600
            if hours_waiting < threshold_hours:
                continue
            reviews = self.client.api(f"repos/{self.repo}/pulls/{pr['number']}/reviews", {"per_page": 100})
            if reviews:
                continue
            reviewers = [(reviewer or {}).get("login") for reviewer in pr.get("requested_reviewers", [])]
            body = (
                f"Open PR #{pr['number']} has been waiting about {int(hours_waiting)} hours without review. "
                f"Requested reviewers: {', '.join(reviewers) if reviewers else 'none'}."
            )
            events.append(
                Event(
                    event_id=_synthetic_event_id("stale_pr_review", pr["number"], updated_at),
                    event_type="stale_pr_review",
                    source="status_probe",
                    occurred_at=utc_now_iso(),
                    repo=self.repo,
                    actor="github-pm-agent",
                    url=pr.get("html_url", ""),
                    title=pr.get("title", ""),
                    body=body,
                    target_kind="pull_request",
                    target_number=pr["number"],
                    metadata={
                        "updated_at": updated_at,
                        "hours_waiting": int(hours_waiting),
                        "author": (pr.get("user") or {}).get("login", ""),
                        "requested_reviewers": reviewers,
                    },
                )
            )
        return events

    def _blocked_issue_stale_events(self) -> List[Event]:
        threshold_hours = self.config.get("engine", {}).get("blocked_issue_stale_hours", 48)
        now = parse_iso8601(utc_now_iso())
        issues = self.client.api(
            f"repos/{self.repo}/issues",
            {"state": "open", "labels": "blocked", "per_page": 100},
        )
        events: List[Event] = []
        for issue in issues:
            if issue.get("pull_request"):
                continue
            updated_at = issue.get("updated_at") or issue.get("created_at") or utc_now_iso()
            hours_blocked = (now - parse_iso8601(updated_at)).total_seconds() / 3600
            if hours_blocked < threshold_hours:
                continue
            body = f"Issue #{issue['number']} has stayed blocked for about {int(hours_blocked)} hours without a new update."
            events.append(
                Event(
                    event_id=_synthetic_event_id("blocked_issue_stale", issue["number"], updated_at),
                    event_type="blocked_issue_stale",
                    source="status_probe",
                    occurred_at=utc_now_iso(),
                    repo=self.repo,
                    actor="github-pm-agent",
                    url=issue.get("html_url", ""),
                    title=issue.get("title", ""),
                    body=body,
                    target_kind="issue",
                    target_number=issue["number"],
                    metadata={
                        "updated_at": updated_at,
                        "hours_blocked": int(hours_blocked),
                        "author": (issue.get("user") or {}).get("login", ""),
                    },
                )
            )
        return events

    def _review_churn_events(self) -> List[Event]:
        pulls = self.client.api(
            f"repos/{self.repo}/pulls",
            {"state": "open", "sort": "updated", "direction": "asc", "per_page": 100},
        )
        events: List[Event] = []
        for pr in pulls:
            if pr.get("draft"):
                continue
            reviews = self.client.api(f"repos/{self.repo}/pulls/{pr['number']}/reviews", {"per_page": 100})
            states = [review.get("state") for review in reviews if review.get("state")]
            if states.count("CHANGES_REQUESTED") < 1:
                continue
            if len(states) < 2:
                continue
            body = (
                f"PR #{pr['number']} has review churn: "
                f"{states.count('CHANGES_REQUESTED')} changes-requested reviews across {len(states)} review events."
            )
            events.append(
                Event(
                    event_id=_synthetic_event_id("review_churn", pr["number"], pr.get("updated_at") or pr.get("created_at") or utc_now_iso()),
                    event_type="review_churn",
                    source="status_probe",
                    occurred_at=utc_now_iso(),
                    repo=self.repo,
                    actor="github-pm-agent",
                    url=pr.get("html_url", ""),
                    title=pr.get("title", ""),
                    body=body,
                    target_kind="pull_request",
                    target_number=pr["number"],
                    metadata={
                        "review_states": states,
                        "requested_reviewers": [(reviewer or {}).get("login") for reviewer in pr.get("requested_reviewers", [])],
                    },
                )
            )
        return events

    def _repeated_ci_instability_events(self) -> List[Event]:
        runs = self.client.api(
            f"repos/{self.repo}/actions/runs",
            {"per_page": 10},
        ).get("workflow_runs", [])
        failed = [run for run in runs if (run.get("conclusion") or "") == "failure"]
        if len(failed) < 2:
            return []
        latest = failed[0]
        body = f"Repeated CI failures detected in the latest workflow window: {len(failed)} failures."
        return [
            Event(
                event_id=_synthetic_event_id("repeated_ci_instability", int(latest.get("run_number") or latest["id"]), latest.get("updated_at") or latest.get("created_at") or utc_now_iso()),
                event_type="repeated_ci_instability",
                source="status_probe",
                occurred_at=utc_now_iso(),
                repo=self.repo,
                actor="github-pm-agent",
                url=latest.get("html_url", ""),
                title=latest.get("name", "workflow"),
                body=body,
                target_kind="workflow_run",
                target_number=latest.get("run_number"),
                metadata={
                    "failed_runs": len(failed),
                    "workflow_name": latest.get("name"),
                },
            )
        ]

    def _stale_discussion_decision_events(self) -> List[Event]:
        discussions = self._discussion_nodes()
        events: List[Event] = []
        for node in discussions:
            updated_at = node.get("updatedAt") or node.get("createdAt") or utc_now_iso()
            age_hours = (parse_iso8601(utc_now_iso()) - parse_iso8601(updated_at)).total_seconds() / 3600
            body = f"{node.get('title', '')} {node.get('body', '')}".lower()
            if age_hours < 24:
                continue
            if not any(token in body for token in ("decide", "decision", "choose", "?")):
                continue
            comment_count = self._discussion_comment_count(node.get("id"))
            if comment_count < 1:
                continue
            events.append(
                Event(
                    event_id=_synthetic_event_id("stale_discussion_decision", node["number"], updated_at),
                    event_type="stale_discussion_decision",
                    source="status_probe",
                    occurred_at=utc_now_iso(),
                    repo=self.repo,
                    actor="github-pm-agent",
                    url=node.get("url", ""),
                    title=node.get("title", ""),
                    body=f"Discussion #{node['number']} needs a decision after {int(age_hours)} hours of inactivity.",
                    target_kind="discussion",
                    target_number=node.get("number"),
                    metadata={
                        "discussion_node_id": node.get("id"),
                        "hours_waiting": int(age_hours),
                        "comment_count": comment_count,
                    },
                )
            )
        return events

    def _docs_drift_before_release_events(self) -> List[Event]:
        release = self._latest_release()
        if not release:
            return []
        compare = self.client.api(
            f"repos/{self.repo}/compare/{release.get('tag_name')}...{self.config.get('github', {}).get('default_branch', 'main')}"
        )
        files = compare.get("files", []) if isinstance(compare, dict) else []
        if not files:
            return []
        if any((file.get("filename") or "").startswith("docs/") or (file.get("filename") or "").endswith("README.md") for file in files):
            return []
        return [
            Event(
                event_id=_synthetic_event_id("docs_drift_before_release", release["id"], release.get("published_at") or utc_now_iso()),
                event_type="docs_drift_before_release",
                source="status_probe",
                occurred_at=utc_now_iso(),
                repo=self.repo,
                actor="github-pm-agent",
                url=release.get("html_url", ""),
                title=release.get("name") or release.get("tag_name") or "release",
                body=f"Release {release.get('tag_name')} has code changes but no docs changes in the compare window.",
                target_kind="release",
                target_number=release.get("id"),
                metadata={"tag_name": release.get("tag_name"), "files_changed": len(files)},
            )
        ]

    def _release_readiness_events(self) -> List[Event]:
        if not self._successful_recent_workflow_runs():
            return []
        release = self._latest_release()
        if not release:
            return []
        return [
            Event(
                event_id=_synthetic_event_id("release_readiness", release["id"], release.get("published_at") or utc_now_iso()),
                event_type="release_readiness",
                source="status_probe",
                occurred_at=utc_now_iso(),
                repo=self.repo,
                actor="github-pm-agent",
                url=release.get("html_url", ""),
                title=release.get("name") or release.get("tag_name") or "release",
                body=f"Repository looks release-ready for {release.get('tag_name')}.",
                target_kind="release",
                target_number=release.get("id"),
                metadata={"tag_name": release.get("tag_name")},
            )
        ]

    def _discussion_nodes(self) -> List[Dict[str, Any]]:
        owner, name = self.repo.split("/", 1)
        query = """
        query($owner: String!, $name: String!, $after: String, $first: Int!) {
          repository(owner: $owner, name: $name) {
            discussions(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo { hasNextPage endCursor }
              nodes { id number title body url createdAt updatedAt }
            }
          }
        }
        """
        return list(
            self.client.iter_graphql_nodes(
                query,
                {"owner": owner, "name": name},
                connection_path=("data", "repository", "discussions"),
                cursor_variable="after",
                page_size_variable="first",
                page_size=25,
            )
        )

    def _discussion_comment_count(self, discussion_id: Any) -> int:
        if not discussion_id:
            return 0
        query = """
        query($discussionId: ID!, $before: String, $last: Int!) {
          node(id: $discussionId) {
            ... on Discussion {
              comments(last: $last, before: $before) { nodes { id } }
            }
          }
        }
        """
        comments = list(
            self.client.iter_graphql_nodes(
                query,
                {"discussionId": discussion_id},
                connection_path=("data", "node", "comments"),
                cursor_variable="before",
                page_size_variable="last",
                page_size=50,
                reverse=True,
            )
        )
        return len(comments)

    def _latest_release(self) -> Dict[str, Any]:
        releases = self.client.api(f"repos/{self.repo}/releases", {"per_page": 1})
        if isinstance(releases, list) and releases:
            return releases[0]
        return {}

    def _blocked_issue_count(self) -> int:
        issues = self.client.api(
            f"repos/{self.repo}/issues",
            {"state": "open", "labels": "blocked", "per_page": 100},
        )
        return len([issue for issue in issues if not issue.get("pull_request")])

    def _stale_pr_count(self) -> int:
        threshold_hours = 48
        now = parse_iso8601(utc_now_iso())
        pulls = self.client.api(
            f"repos/{self.repo}/pulls",
            {"state": "open", "sort": "updated", "direction": "asc", "per_page": 100},
        )
        count = 0
        for pr in pulls:
            if pr.get("draft"):
                continue
            updated_at = pr.get("updated_at") or pr.get("created_at") or utc_now_iso()
            hours_waiting = (now - parse_iso8601(updated_at)).total_seconds() / 3600
            if hours_waiting < threshold_hours:
                continue
            reviews = self.client.api(f"repos/{self.repo}/pulls/{pr['number']}/reviews", {"per_page": 100})
            if reviews:
                continue
            count += 1
        return count

    def _successful_recent_workflow_runs(self) -> bool:
        runs = self.client.api(
            f"repos/{self.repo}/actions/runs",
            {"per_page": 10},
        ).get("workflow_runs", [])
        return any((run.get("conclusion") or "") == "success" for run in runs)
