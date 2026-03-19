from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from github_pm_agent.models import Event
from github_pm_agent.utils import append_jsonl, read_json, read_jsonl, utc_now_iso, write_json, write_jsonl


class QueueStore:
    _QUEUE_METADATA_KEY = "_queue"

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

    def _read_terminal(self, path: Path) -> List[Dict[str, Any]]:
        return read_jsonl(path)

    def _write_terminal(self, path: Path, items: Iterable[Dict[str, Any]]) -> None:
        write_jsonl(path, items)

    def _seen_ids(self) -> List[str]:
        return read_json(self.seen_path, [])

    def _pending_event_ids(self) -> set[str]:
        return {
            item.get("event_id")
            for item in self._read_pending()
            if isinstance(item, dict) and item.get("event_id")
        }

    def _event_attempt(self, payload: Dict[str, Any]) -> int:
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            return 1
        queue_metadata = metadata.get(self._QUEUE_METADATA_KEY, {})
        if not isinstance(queue_metadata, dict):
            return 1
        attempt = queue_metadata.get("attempt", 1)
        if not isinstance(attempt, int) or attempt < 1:
            return 1
        return attempt

    def _event_with_queue_metadata(
        self,
        event: Event,
        *,
        attempt: int,
        requeued_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = event.to_dict()
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        queue_metadata = metadata.get(self._QUEUE_METADATA_KEY, {})
        if not isinstance(queue_metadata, dict):
            queue_metadata = {}
        queue_metadata = dict(queue_metadata)
        queue_metadata["attempt"] = max(attempt, 1)
        if requeued_from is not None:
            queue_metadata["requeued_from"] = requeued_from
            queue_metadata["requeued_at"] = utc_now_iso()
        metadata[self._QUEUE_METADATA_KEY] = queue_metadata
        payload["metadata"] = metadata
        return payload

    def _terminal_records(
        self,
        path: Path,
        *,
        limit: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        records = self._read_terminal(path)
        if event_id is not None:
            records = [
                record
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get("event"), dict)
                and record["event"].get("event_id") == event_id
            ]
        if limit is not None and limit <= 0:
            return []
        if limit is not None:
            records = records[-limit:]
        return records

    def _requeue_terminal_records(
        self,
        path: Path,
        *,
        source_name: str,
        event_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        records = self._read_terminal(path)
        matching_indexes = [
            index
            for index, record in enumerate(records)
            if event_id is None
            or (
                isinstance(record, dict)
                and isinstance(record.get("event"), dict)
                and record["event"].get("event_id") == event_id
            )
        ]
        if limit is not None and limit <= 0:
            matching_indexes = []
        elif limit is not None:
            matching_indexes = matching_indexes[-limit:]
        selected_indexes = set(matching_indexes)
        pending_ids = self._pending_event_ids()
        retained: List[Dict[str, Any]] = []
        requeued_event_ids: List[str] = []
        requeued = 0
        skipped = 0
        for index, record in enumerate(records):
            if index not in selected_indexes:
                retained.append(record)
                continue
            event_payload = record.get("event") if isinstance(record, dict) else None
            if not isinstance(event_payload, dict):
                retained.append(record)
                skipped += 1
                continue
            event = Event.from_dict(event_payload)
            if event.event_id in pending_ids:
                retained.append(record)
                skipped += 1
                continue
            next_attempt = self._event_attempt(event_payload) + 1
            append_jsonl(
                self.pending_path,
                self._event_with_queue_metadata(
                    event,
                    attempt=next_attempt,
                    requeued_from=source_name,
                ),
            )
            pending_ids.add(event.event_id)
            requeued += 1
            requeued_event_ids.append(event.event_id)
        self._write_terminal(path, retained)
        return {
            "source": source_name,
            "requested": len(matching_indexes),
            "requeued": requeued,
            "skipped": skipped,
            "event_ids": requeued_event_ids,
        }

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
            append_jsonl(
                self.pending_path,
                self._event_with_queue_metadata(event, attempt=1),
            )
            self.remember(event.event_id)
            count += 1
        return count

    def list_pending(self, limit: Optional[int] = None, event_id: Optional[str] = None) -> List[Event]:
        raw = self._read_pending()
        if event_id is not None:
            raw = [item for item in raw if item.get("event_id") == event_id]
        if limit is not None:
            raw = raw[:limit]
        return [Event.from_dict(item) for item in raw]

    def peek(self, limit: int = 10) -> List[Event]:
        return self.list_pending(limit=limit)

    def list_done(self, limit: Optional[int] = None, event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._terminal_records(self.done_path, limit=limit, event_id=event_id)

    def list_dead(self, limit: Optional[int] = None, event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._terminal_records(self.dead_path, limit=limit, event_id=event_id)

    def pop(self) -> Optional[Event]:
        raw = self._read_pending()
        if not raw:
            return None
        first = raw[0]
        self._write_pending(raw[1:])
        return Event.from_dict(first)

    def mark_done(self, event: Event, result: Dict) -> None:
        append_jsonl(
            self.done_path,
            {
                "event": self._event_with_queue_metadata(
                    event,
                    attempt=self._event_attempt(event.to_dict()),
                ),
                "result": result,
                "done_at": utc_now_iso(),
            },
        )

    def mark_failed(self, event: Event, error: str) -> None:
        append_jsonl(
            self.dead_path,
            {
                "event": self._event_with_queue_metadata(
                    event,
                    attempt=self._event_attempt(event.to_dict()),
                ),
                "error": error,
                "failed_at": utc_now_iso(),
            },
        )

    def retry_dead(self, event_id: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        return self._requeue_terminal_records(
            self.dead_path,
            source_name="dead",
            event_id=event_id,
            limit=limit,
        )

    def replay_done(self, event_id: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        return self._requeue_terminal_records(
            self.done_path,
            source_name="done",
            event_id=event_id,
            limit=limit,
        )
