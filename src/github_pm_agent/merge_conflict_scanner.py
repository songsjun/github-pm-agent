from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from github_pm_agent.utils import append_jsonl, utc_now_iso
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

            append_jsonl(
                self.queue.pending_path,
                self._build_requeued_event(
                    original_event,
                    instance.get_artifacts(),
                    reason="merge_conflict_scan",
                    human_comment=conflict_reason,
                    response_type="merge_conflict",
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
        base_sha = ((pr_state.get("base") or {}).get("sha") or "").strip()
        mergeable_state = str(pr_state.get("mergeable_state") or "").strip().lower()
        return f"{pr_number}:{head_sha}:{base_sha}:{mergeable_state}"

    def _build_requeued_event(
        self,
        original_event: Dict[str, Any],
        artifacts: Dict[str, Any],
        *,
        reason: str,
        human_comment: str,
        response_type: str,
    ) -> Dict[str, Any]:
        resumed = dict(original_event)
        resumed_metadata = dict(original_event.get("metadata", {}))
        resumed_metadata["advance_to_phase"] = "fix_iteration"
        resumed_metadata["artifacts"] = artifacts
        resumed_metadata["gate_human_comment"] = human_comment
        resumed_metadata["gate_response_type"] = response_type

        queue_meta = dict(resumed_metadata.get("_queue", {}))
        previous_attempt = queue_meta.get("attempt", 1)
        if not isinstance(previous_attempt, int) or previous_attempt < 1:
            previous_attempt = 1
        queue_meta["attempt"] = previous_attempt + 1
        queue_meta["requeued_from"] = reason
        queue_meta["requeued_at"] = utc_now_iso()
        resumed_metadata["_queue"] = queue_meta

        original_event_id = str(original_event.get("event_id", "resume"))
        seed = (
            f"{original_event_id}:{reason}:fix_iteration:"
            f"{queue_meta['attempt']}:{queue_meta['requeued_at']}"
        )
        resumed["event_id"] = f"resume:{hashlib.sha1(seed.encode('utf-8')).hexdigest()}"
        resumed["metadata"] = resumed_metadata
        return resumed
