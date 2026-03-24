from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class WorkflowInstance:
    """Persistent state for a single discussion's workflow progression."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self._state: Dict[str, Any] = {}
        if state_path.exists():
            try:
                self._state = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._state = {}

    @classmethod
    def load(cls, runtime_dir: Path, repo: str, number: int) -> "WorkflowInstance":
        safe_repo = repo.replace("/", "__")
        path = runtime_dir / "workflows" / safe_repo / str(number) / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path)

    def get_phase(self) -> Optional[str]:
        return self._state.get("phase")

    def set_phase(self, phase: str) -> None:
        self._state["phase"] = phase
        self._save()

    def get_artifacts(self) -> Dict[str, str]:
        return dict(self._state.get("artifacts", {}))

    def set_artifact(self, phase: str, text: str) -> None:
        self._state.setdefault("artifacts", {})[phase] = text
        self._save()

    def get_original_event(self) -> Optional[Dict[str, Any]]:
        raw = self._state.get("original_event")
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw if isinstance(raw, dict) else None

    def set_original_event(self, event_dict: Dict[str, Any]) -> None:
        self._state["original_event"] = event_dict
        self._save()

    def get_gate_issue_number(self) -> Optional[int]:
        return self._state.get("gate_issue_number")

    def get_gate_next_phase(self) -> Optional[str]:
        return self._state.get("gate_next_phase")

    def set_gate(self, issue_number: int, next_phase: str = "") -> None:
        self._state["gate_issue_number"] = issue_number
        if next_phase:
            self._state["gate_next_phase"] = next_phase
        self._save()

    def set_discussion_gate(self, node_id: str, posted_at: str, next_phase: str) -> None:
        """Gate tracked via Discussion comment (no issue created)."""
        self._state["gate_discussion_node_id"] = node_id
        self._state["gate_posted_at"] = posted_at
        self._state["gate_next_phase"] = next_phase
        self._save()

    def get_discussion_gate_node_id(self) -> Optional[str]:
        return self._state.get("gate_discussion_node_id")

    def get_gate_posted_at(self) -> Optional[str]:
        return self._state.get("gate_posted_at")

    def clear_gate(self) -> None:
        self._state.pop("gate_issue_number", None)
        self._state.pop("gate_next_phase", None)
        self._state.pop("gate_discussion_node_id", None)
        self._state.pop("gate_posted_at", None)
        self._save()

    # --- Clarification (intra-phase suspension) ---

    def set_clarification(self, phase: str, posted_at: str, node_id: str = "") -> None:
        """Record that we've posted clarification questions and are waiting for owner reply."""
        self._state["clarification_phase"] = phase
        self._state["clarification_posted_at"] = posted_at
        if node_id:
            self._state["clarification_node_id"] = node_id
        self._save()

    def get_clarification(self) -> Optional[Dict[str, Any]]:
        if "clarification_phase" not in self._state:
            return None
        return {
            "phase": self._state["clarification_phase"],
            "posted_at": self._state["clarification_posted_at"],
            "node_id": self._state.get("clarification_node_id", ""),
        }

    def clear_clarification(self) -> None:
        self._state.pop("clarification_phase", None)
        self._state.pop("clarification_posted_at", None)
        self._state.pop("clarification_node_id", None)
        self._save()

    def is_awaiting_clarification(self) -> bool:
        return "clarification_phase" in self._state

    def add_user_supplement(self, phase: str, content: str) -> None:
        """Accumulate owner additions/changes across gate confirmations."""
        self._state.setdefault("user_supplements", []).append({"phase": phase, "content": content})
        self._save()

    def get_user_supplements(self) -> list:
        return list(self._state.get("user_supplements", []))

    def add_pending_comment(self, comment: str) -> None:
        self._state.setdefault("pending_comments", []).append(comment)
        self._save()

    def get_pending_comments(self) -> List[str]:
        return list(self._state.get("pending_comments", []))

    def clear_pending_comments(self) -> None:
        self._state.pop("pending_comments", None)
        self._save()

    def set_created_issue_refs(self, refs: List[Dict[str, Any]]) -> None:
        self._state["created_issue_refs"] = refs
        self._save()

    def get_created_issue_refs(self) -> List[Dict[str, Any]]:
        return list(self._state.get("created_issue_refs", []))

    def set_completion_comment_posted(self) -> None:
        self._state["completion_comment_posted"] = True
        self._save()

    def is_completion_comment_posted(self) -> bool:
        return bool(self._state.get("completion_comment_posted"))

    def set_terminated(self, reason: str = "") -> None:
        self._state["terminated"] = True
        if reason:
            self._state["terminated_reason"] = reason
        self._save()

    def is_terminated(self) -> bool:
        return bool(self._state.get("terminated"))

    def get_terminated_reason(self) -> str:
        return str(self._state.get("terminated_reason", ""))

    def get_review_round(self) -> int:
        """Return the current code-review / fix iteration count (0-indexed)."""
        return int(self._state.get("review_round", 0))

    def set_review_round(self, round_num: int) -> None:
        self._state["review_round"] = round_num
        self._save()

    def get_workflow_type(self) -> Optional[str]:
        return self._state.get("workflow_type") or None

    def set_workflow_type(self, workflow_type: str) -> None:
        self._state["workflow_type"] = workflow_type
        self._save()

    def reset_for_workflow_type(self, workflow_type: str) -> None:
        """Clear all state and start fresh for a new workflow type."""
        self._state = {"workflow_type": workflow_type}
        self._save()

    def is_completed(self) -> bool:
        return bool(self._state.get("completed"))

    def set_completed(self) -> None:
        self._state["completed"] = True
        self._save()

    def _save(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
