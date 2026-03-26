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

            if instance.get_workflow_type() != "issue_coding":
                continue

            pr_number = self._artifact_pr_number(instance)
            if pr_number is None:
                continue

            pr_state = self._load_pull_request_state(repo, pr_number)
            if instance.is_terminated():
                termination_reason = instance.get_terminated_reason()
                if self._should_close_terminated_pr(pr_state, termination_reason):
                    try:
                        self.client.api(
                            f"repos/{repo}/pulls/{pr_number}",
                            {"state": "closed"},
                            method="PATCH",
                        )
                    except Exception:
                        continue
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
                            "synced_state": "closed_open_pr_after_workflow_failure",
                        }
                    )
                continue

            if instance.is_completed():
                continue

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

    @staticmethod
    def _should_close_terminated_pr(pr_state: Dict[str, Any], termination_reason: str) -> bool:
        if not IssueCodingSyncScanner._pull_request_is_open(pr_state):
            return False
        normalized_reason = (termination_reason or "").lower()
        if any(fragment in normalized_reason for fragment in IssueCodingSyncScanner._manual_followup_reason_fragments()):
            return False
        return any(fragment in normalized_reason for fragment in IssueCodingSyncScanner._auto_close_reason_fragments())

    @staticmethod
    def _pull_request_is_open(pr_state: Dict[str, Any]) -> bool:
        return str(pr_state.get("state") or "").strip().lower() == "open"

    @staticmethod
    def _manual_followup_reason_fragments() -> tuple[str, ...]:
        return (
            "automatic gate limit",
            "manual intervention",
            "not machine-verifiable",
            "parse failure",
            "iteration error",
            "coding session failed",
        )

    @staticmethod
    def _auto_close_reason_fragments() -> tuple[str, ...]:
        return (
            "tests failed after",
            "fix tests failed",
            "merge conflict resolution failed",
            "code review exceeded",
        )
