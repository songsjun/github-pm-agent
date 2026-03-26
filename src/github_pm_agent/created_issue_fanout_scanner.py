from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from github_pm_agent.models import Event
from github_pm_agent.workflow_instance import WorkflowInstance


class CreatedIssueFanoutScanner:
    """Enqueue issue_coding events for ready-to-code issues created by a completed discussion workflow."""

    def __init__(self, queue: Any, client: Any) -> None:
        self.queue = queue
        self.client = client

    def scan_and_enqueue(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        pending_keys: Set[Tuple[str, int]] = {
            (event.repo, int(event.target_number))
            for event in self.queue.list_pending()
            if event.event_type == "issue_coding" and event.target_number is not None
        }
        active_issue_coding_keys: Set[Tuple[str, int]] = set()
        for state_path in workflows_dir.glob("*/*/state.json"):
            repo = state_path.parts[-3].replace("__", "/", 1)
            number = int(state_path.parts[-2])
            instance = WorkflowInstance(state_path)
            if instance.get_workflow_type() == "issue_coding":
                active_issue_coding_keys.add((repo, number))

        results: List[Dict[str, Any]] = []
        for state_path in workflows_dir.glob("*/*/state.json"):
            repo = state_path.parts[-3].replace("__", "/", 1)
            discussion_number = int(state_path.parts[-2])
            instance = WorkflowInstance(state_path)
            if instance.get_workflow_type() != "discussion" or not instance.is_completed():
                continue

            for ref in instance.get_created_issue_refs():
                issue_number = ref.get("number")
                if not isinstance(issue_number, int):
                    continue
                workflow_key = (repo, issue_number)
                if workflow_key in pending_keys or workflow_key in active_issue_coding_keys:
                    continue

                issue = self.client.api(f"repos/{repo}/issues/{issue_number}", method="GET")
                if not isinstance(issue, dict) or issue.get("state") != "open":
                    continue
                labels = [
                    str((label or {}).get("name") or "").strip()
                    for label in issue.get("labels", [])
                    if isinstance(label, dict)
                ]
                if "ready-to-code" not in labels:
                    continue

                event = Event(
                    event_id=f"created-issue-fanout:{repo}:{issue_number}",
                    event_type="issue_coding",
                    source="workflow_fanout",
                    occurred_at=str(issue.get("updated_at") or issue.get("created_at") or ""),
                    repo=repo,
                    actor=str((issue.get("user") or {}).get("login") or "github-pm-agent"),
                    url=str(issue.get("html_url") or ""),
                    title=str(issue.get("title") or ""),
                    body=str(issue.get("body") or ""),
                    target_kind="issue",
                    target_number=issue_number,
                    metadata={"labels": labels, "action": "opened"},
                )
                if self.queue.enqueue([event]):
                    pending_keys.add(workflow_key)
                    results.append(
                        {
                            "repo": repo,
                            "discussion_number": discussion_number,
                            "issue_number": issue_number,
                            "reason": "created_issue_missing_issue_coding_event",
                            "event_id": event.event_id,
                        }
                    )

        return results
