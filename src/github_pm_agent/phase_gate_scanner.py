from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from github_pm_agent.utils import append_jsonl, read_jsonl, utc_now_iso
from github_pm_agent.workflow_instance import WorkflowInstance


class PhaseGateScanner:
    """Watches workflow-gate issues; re-queues discussion events when gates are resolved."""

    def __init__(self, queue: Any, client: Any, owner_login: str) -> None:
        self.queue = queue
        self.client = client
        self.owner_login = owner_login
        self.advanced_path = queue.runtime_dir / "gate_advanced.jsonl"

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
            if instance.is_terminated() or instance.is_completed():
                continue

            next_phase = instance.get_gate_next_phase()
            if not next_phase:
                continue

            # Discussion-based gate (preferred)
            discussion_node_id = instance.get_discussion_gate_node_id()
            gate_posted_at = instance.get_gate_posted_at()
            if discussion_node_id and gate_posted_at:
                gate_key = f"{repo}:discussion:{number}:{next_phase}"
                if gate_key in already_advanced:
                    continue
                owner, name = (repo.split("/", 1) + [""])[:2]
                human_comment = self._check_discussion_gate_resolved(owner, name, number, gate_posted_at)
                if human_comment is None:
                    continue
                self._advance(instance, repo, number, next_phase, human_comment, gate_key)
                results.append({"repo": repo, "discussion_number": number, "from_phase": instance.get_phase(), "to_phase": next_phase})
                continue

            # Legacy issue-based gate
            gate_issue_number = instance.get_gate_issue_number()
            if gate_issue_number is None or (repo, gate_issue_number) in already_advanced:
                continue
            human_comment = self._check_issue_gate_resolved(gate_issue_number, repo)
            if human_comment is None:
                continue
            self._advance(instance, repo, number, next_phase, human_comment, (repo, gate_issue_number))
            results.append({"repo": repo, "discussion_number": number, "from_phase": instance.get_phase(), "to_phase": next_phase})

        return results

    def _advance(
        self,
        instance: WorkflowInstance,
        repo: str,
        number: int,
        next_phase: str,
        human_comment: str,
        gate_key: Any,
    ) -> None:
        original_event = instance.get_original_event()
        if not original_event:
            return
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
                "gate_key": str(gate_key),
                "repo": repo,
                "discussion_number": number,
                "from_phase": current_phase,
                "to_phase": next_phase,
                "advanced_at": utc_now_iso(),
            },
        )
        instance.clear_gate()

    def _already_advanced(self) -> Set[Any]:
        result = set()
        for item in read_jsonl(self.advanced_path):
            gate_key = item.get("gate_key")
            if gate_key:
                result.add(gate_key)
            # legacy format
            repo = item.get("repo")
            gate_issue = item.get("gate_issue_number")
            if repo and gate_issue is not None:
                result.add((repo, gate_issue))
        return result

    def _check_discussion_gate_resolved(
        self, owner: str, name: str, number: int, gate_posted_at: str
    ) -> Optional[str]:
        """Return owner's comment text if they replied after gate_posted_at, else None."""
        if not self.owner_login:
            return None
        try:
            comments = self.client.get_discussion_comments(owner, name, number)
        except Exception:
            return None
        for comment in comments:
            if comment.get("createdAt", "") <= gate_posted_at:
                continue
            login = (comment.get("author") or {}).get("login", "")
            if login == self.owner_login:
                return comment.get("body") or ""
        return None

    def _check_issue_gate_resolved(self, issue_number: int, repo: str) -> Optional[str]:
        """Return human comment text if gate issue is resolved, None if still open."""
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
