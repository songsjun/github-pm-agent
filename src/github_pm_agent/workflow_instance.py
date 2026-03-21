from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


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

    def clear_gate(self) -> None:
        self._state.pop("gate_issue_number", None)
        self._state.pop("gate_next_phase", None)
        self._save()

    def _save(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
