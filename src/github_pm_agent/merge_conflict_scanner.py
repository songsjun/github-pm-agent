from __future__ import annotations

from typing import Any, Dict, List, Optional

from github_pm_agent.queue_store import enqueue_pending_payload
from github_pm_agent.utils import build_requeued_event
from github_pm_agent.workflow_instance import WorkflowInstance


class MergeConflictScanner:
    """Detect merge conflicts in active issue-coding workflows and requeue fixes."""

    SCANNED_PHASES = {"code_review", "pm_decision"}

    def __init__(self, queue: Any, client: Any, actions: Any, config: Dict[str, Any]) -> None:
        self.queue = queue
        self.client = client
        self.actions = actions
        self.config = config

    def scan_and_requeue(self) -> List[Dict[str, Any]]:
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

            phase = instance.get_phase() or ""
            if phase not in self.SCANNED_PHASES:
                continue

            pr_number = self._artifact_pr_number(instance)
            if pr_number is None:
                continue

            pr_state = self._load_pull_request_state(repo, pr_number)
            if str(pr_state.get("state") or "").lower() != "open":
                continue
            if not self._pull_request_has_merge_conflict(pr_state):
                continue

            signature = self._conflict_signature(pr_number, pr_state)
            if signature == instance.get_last_merge_conflict_signature():
                continue

            conflict_reason = (
                f"PR #{pr_number} no longer merges cleanly against "
                f"`{self.config.get('github', {}).get('default_branch', 'main')}`. "
                "This was detected automatically after repository state changed. "
                "Rebase or update the branch on the latest main, resolve conflicts, rerun tests, "
                "and return to review."
            )

            self.actions.comment("issue", issue_number, conflict_reason)
            if instance.get_pending_comments():
                instance.clear_pending_comments()
            if instance.get_gate_issue_number() or instance.get_discussion_gate_node_id():
                instance.clear_gate()
            instance.set_last_merge_conflict_signature(signature)

            original_event = instance.get_original_event()
            if not original_event:
                instance.set_terminated("Missing original event for merge conflict recovery")
                continue

            enqueue_pending_payload(
                self.queue.runtime_dir,
                build_requeued_event(
                    original_event,
                    metadata={
                        **dict(original_event.get("metadata", {})),
                        "advance_to_phase": "fix_iteration",
                        "artifacts": instance.get_artifacts(),
                        "gate_human_comment": conflict_reason,
                        "gate_response_type": "merge_conflict",
                    },
                    reason="merge_conflict_scan",
                ),
            )
            results.append(
                {
                    "repo": repo,
                    "issue_number": issue_number,
                    "pr_number": pr_number,
                    "from_phase": phase,
                    "to_phase": "fix_iteration",
                    "reason": "merge_conflict",
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
    def _pull_request_has_merge_conflict(pr_state: Dict[str, Any]) -> bool:
        mergeable_state = str(pr_state.get("mergeable_state") or "").strip().lower()
        mergeable = pr_state.get("mergeable")
        return mergeable_state == "dirty" or mergeable is False

    @staticmethod
    def _conflict_signature(pr_number: int, pr_state: Dict[str, Any]) -> str:
        head_sha = ((pr_state.get("head") or {}).get("sha") or "").strip()
        mergeable_state = str(pr_state.get("mergeable_state") or "").strip().lower()
        return f"{pr_number}:{head_sha}:{mergeable_state}"
