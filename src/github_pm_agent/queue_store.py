from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

from github_pm_agent.models import Event
from github_pm_agent.utils import append_jsonl, read_json, read_jsonl, write_json, write_jsonl


class QueueStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.pending_path = runtime_dir / "queue_pending.jsonl"
        self.done_path = runtime_dir / "queue_done.jsonl"
        self.dead_path = runtime_dir / "queue_dead.jsonl"
        self.seen_path = runtime_dir / "seen_ids.json"

    def _read_pending(self) -> List[Dict]:
        return read_jsonl(self.pending_path)

    def _write_pending(self, items: Iterable[Dict]) -> None:
        write_jsonl(self.pending_path, items)

    def _seen_ids(self) -> List[str]:
        return read_json(self.seen_path, [])

    def has_seen(self, event_id: str) -> bool:
        return event_id in set(self._seen_ids())

    def remember(self, event_id: str) -> None:
        seen = self._seen_ids()
        if event_id not in seen:
            seen.append(event_id)
            write_json(self.seen_path, seen)

    def enqueue(self, events: Iterable[Event]) -> int:
        count = 0
        for event in events:
            if self.has_seen(event.event_id):
                continue
            append_jsonl(self.pending_path, event.to_dict())
            self.remember(event.event_id)
            count += 1
        return count

    def list_pending(self, limit: Optional[int] = None) -> List[Event]:
        raw = self._read_pending()
        if limit is not None:
            raw = raw[:limit]
        return [Event.from_dict(item) for item in raw]

    def peek(self, limit: int = 10) -> List[Event]:
        return self.list_pending(limit=limit)

    def pop(self) -> Optional[Event]:
        raw = self._read_pending()
        if not raw:
            return None
        first = raw[0]
        self._write_pending(raw[1:])
        return Event.from_dict(first)

    def mark_done(self, event: Event, result: Dict) -> None:
        append_jsonl(self.done_path, {"event": event.to_dict(), "result": result})

    def mark_failed(self, event: Event, error: str) -> None:
        append_jsonl(self.dead_path, {"event": event.to_dict(), "error": error})

