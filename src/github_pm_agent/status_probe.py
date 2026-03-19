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
