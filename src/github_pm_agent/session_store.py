from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from github_pm_agent.utils import append_jsonl, ensure_dir, read_jsonl, utc_now_iso


class SessionStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.sessions_dir = runtime_dir / "sessions"
        ensure_dir(self.sessions_dir)

    def path_for(self, session_key: str) -> Path:
        return self.sessions_dir / f"{session_key}.jsonl"

    def recent_transcript(self, session_key: str, limit: int = 4, max_chars: int = 12000) -> List[Dict]:
        items = read_jsonl(self.path_for(session_key))
        selected: List[Dict] = []
        total_chars = 0
        for item in reversed(items):
            chunk_size = len(item.get("request", "")) + len(item.get("response", "")) + 32
            if selected and (len(selected) >= limit or total_chars + chunk_size > max_chars):
                break
            selected.append(item)
            total_chars += chunk_size
            if len(selected) >= limit:
                break
        selected.reverse()
        return selected

    def append_turn(self, session_key: str, request: str, response: str) -> None:
        append_jsonl(
            self.path_for(session_key),
            {
                "captured_at": utc_now_iso(),
                "request": request,
                "response": response,
            },
        )
