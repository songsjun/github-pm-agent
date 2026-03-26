from __future__ import annotations

from typing import Any, Dict, List, Optional

from github_pm_agent.workflow_instance import WorkflowInstance


class IssueCodingSyncScanner:
    """Reconcile workflow state with externally changed PR/issue state."""

    def __init__(self, queue: Any, client: Any, actions: Any) -> None:
        self.queue = queue
        self.client = client
        self.actions = actions

    def scan_and_sync(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        results: List[Dict[str, Any]] = []
        for state_path in workflows_dir.glob("*/*/state.json"):
            repo = state_path.parts[-3].replace("__", "/", 1)
            issue_number = int(state_path.parts[-2])
            instance = WorkflowInstance(state_path)

            if instance.is_completed() or instance.is_terminated():
                continue
            if instance.get_workflow_type() != "issue_coding":
                continue

            pr_number = self._artifact_pr_number(instance)
            if pr_number is None:
                continue

            pr_state = self._load_pull_request_state(repo, pr_number)
            if not self._pull_request_is_merged(pr_state):
                continue

            if instance.get_gate_issue_number() or instance.get_discussion_gate_node_id():
                instance.clear_gate()
            if instance.get_pending_comments():
                instance.clear_pending_comments()
            if not instance.is_completion_comment_posted():
                instance.set_completion_comment_posted()
            instance.set_completed()

            try:
                self.actions.remove_labels(issue_number, ["ready-to-code"])
            except Exception:
                pass

            results.append(
                {
                    "repo": repo,
                    "issue_number": issue_number,
                    "pr_number": pr_number,
                    "phase": instance.get_phase(),
                    "synced_state": "completed_from_merged_pr",
                }
            )

        return results

    @staticmethod
    def _artifact_pr_number(instance: WorkflowInstance) -> Optional[int]:
        raw_pr = instance.get_artifacts().get("pr_number", "")
        if not str(raw_pr).strip().isdigit():
            return None
        return int(str(raw_pr))

    def _load_pull_request_state(self, repo: str, pr_number: int) -> Dict[str, Any]:
        try:
            payload = self.client.api(f"repos/{repo}/pulls/{pr_number}", method="GET")
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _pull_request_is_merged(pr_state: Dict[str, Any]) -> bool:
        merged_at = pr_state.get("merged_at") or pr_state.get("mergedAt")
        merged = pr_state.get("merged")
        return bool(merged_at) or merged is True
