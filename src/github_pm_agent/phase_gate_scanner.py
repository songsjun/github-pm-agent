from __future__ import annotations

from typing import Any, Dict, List, Optional

from github_pm_agent.utils import append_jsonl, read_jsonl, utc_now_iso
from github_pm_agent.workflow_instance import WorkflowInstance


class PhaseGateScanner:
    """Watches workflow-gate issues; re-queues discussion events when gates are resolved."""

    def __init__(self, queue: Any, client: Any, owner_login: str) -> None:
        self.queue = queue
        self.client = client
        self.owner_login = owner_login
        self.advanced_path = queue.runtime_dir / "gate_advanced.jsonl"

    def _already_advanced(self) -> set:
        return {
            item["gate_issue_number"]
            for item in read_jsonl(self.advanced_path)
            if item.get("gate_issue_number") is not None
        }

    def scan_and_advance(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        already_advanced = self._already_advanced()
        results: List[Dict[str, Any]] = []

        for state_path in workflows_dir.glob("*/*/state.json"):
            # path structure: workflows/{safe_repo}/{number}/state.json
            parts = state_path.parts
            number_str = parts[-2]
            safe_repo = parts[-3]
            repo = safe_repo.replace("__", "/", 1)

            try:
                number = int(number_str)
            except ValueError:
                continue

            instance = WorkflowInstance(state_path)
            gate_issue_number = instance.get_gate_issue_number()
            if gate_issue_number is None or gate_issue_number in already_advanced:
                continue

            human_comment = self._check_gate_resolved(gate_issue_number, repo)
            if human_comment is None:
                continue

            next_phase = instance.get_gate_next_phase()
            if not next_phase:
                continue

            original_event = instance.get_original_event()
            if not original_event:
                continue

            current_phase = instance.get_phase()
            new_metadata = dict(original_event.get("metadata", {}))
            new_metadata["advance_to_phase"] = next_phase
            new_metadata["artifacts"] = instance.get_artifacts()
            new_metadata["gate_human_comment"] = human_comment
            resumed_event_dict = {**original_event, "metadata": new_metadata}

            append_jsonl(self.queue.pending_path, resumed_event_dict)
            append_jsonl(
                self.advanced_path,
                {
                    "gate_issue_number": gate_issue_number,
                    "repo": repo,
                    "discussion_number": number,
                    "from_phase": current_phase,
                    "to_phase": next_phase,
                    "advanced_at": utc_now_iso(),
                },
            )
            instance.clear_gate()
            results.append(
                {
                    "repo": repo,
                    "discussion_number": number,
                    "from_phase": current_phase,
                    "to_phase": next_phase,
                }
            )

        return results

    def _check_gate_resolved(self, issue_number: int, repo: str) -> Optional[str]:
        """Return human comment text if gate is resolved, None if still open."""
        if self.owner_login:
            comments = self.client.api(f"repos/{repo}/issues/{issue_number}/comments", method="GET")
            if isinstance(comments, list):
                for comment in comments:
                    login = (comment.get("user") or {}).get("login", "")
                    if login == self.owner_login:
                        return comment.get("body") or ""

        issue = self.client.api(f"repos/{repo}/issues/{issue_number}", method="GET")
        if isinstance(issue, dict) and issue.get("state") == "closed":
            return ""
        return None
