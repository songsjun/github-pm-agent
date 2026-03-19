from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from github_pm_agent.utils import append_jsonl, ensure_dir, read_jsonl


class SessionStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.sessions_dir = runtime_dir / "sessions"
        ensure_dir(self.sessions_dir)

    def path_for(self, session_key: str) -> Path:
        return self.sessions_dir / f"{session_key}.jsonl"

    def recent_transcript(self, session_key: str, limit: int = 6) -> List[Dict]:
        items = read_jsonl(self.path_for(session_key))
        return items[-limit:]

    def append_turn(self, session_key: str, request: str, response: str) -> None:
        append_jsonl(
            self.path_for(session_key),
            {
                "request": request,
                "response": response,
            },
        )

