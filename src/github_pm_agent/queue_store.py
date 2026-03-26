from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from github_pm_agent.models import Event
from github_pm_agent.utils import (
    append_jsonl,
    ensure_dir,
    read_json,
    read_jsonl,
    utc_now_iso,
    write_json,
    write_jsonl,
)


def _pending_lock_path(runtime_dir: Path) -> Path:
    return runtime_dir / "queue_pending.lock"


@contextmanager
def pending_queue_lock(runtime_dir: Path):
    ensure_dir(runtime_dir)
    lock_path = _pending_lock_path(runtime_dir)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def enqueue_pending_payload(
    runtime_dir: Path,
    payload: Dict[str, Any],
    *,
    remember_seen: bool = False,
) -> bool:
    event_id = str(payload.get("event_id", "") or "").strip()
    pending_path = runtime_dir / "queue_pending.jsonl"
    seen_path = runtime_dir / "seen_ids.json"

    with pending_queue_lock(runtime_dir):
        pending = read_jsonl(pending_path)
        if event_id and any(item.get("event_id") == event_id for item in pending if isinstance(item, dict)):
            return False
        if remember_seen and event_id:
            seen = read_json(seen_path, [])
            if event_id in seen:
                return False
            seen.append(event_id)
            write_json(seen_path, seen)
        append_jsonl(pending_path, payload)
        return True


class QueueStore:
    _QUEUE_METADATA_KEY = "_queue"

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.pending_path = runtime_dir / "queue_pending.jsonl"
        self.done_path = runtime_dir / "queue_done.jsonl"
        self.dead_path = runtime_dir / "queue_dead.jsonl"
        self.suspended_path = runtime_dir / "queue_suspended.jsonl"
        self.resumed_path = runtime_dir / "queue_resumed.jsonl"
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
        retained: List[Dict[str, Any]] = []
        requeued_event_ids: List[str] = []
        requeued = 0
        skipped = 0
        with pending_queue_lock(self.runtime_dir):
            pending_ids = self._pending_event_ids()
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
            if enqueue_pending_payload(
                self.runtime_dir,
                self._event_with_queue_metadata(event, attempt=1),
                remember_seen=True,
            ):
                count += 1
        return count

    def list_pending(self, limit: Optional[int] = None, event_id: Optional[str] = None) -> List[Event]:
        with pending_queue_lock(self.runtime_dir):
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
        with pending_queue_lock(self.runtime_dir):
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

    def mark_suspended(
        self,
        event: Event,
        escalation_issue_number: int,
        escalation_key: str,
        reason_class: str,
    ) -> None:
        append_jsonl(
            self.suspended_path,
            {
                "event": event.to_dict(),
                "escalation_issue_number": escalation_issue_number,
                "escalation_key": escalation_key,
                "reason_class": reason_class,
                "suspended_at": utc_now_iso(),
            },
        )

    def list_suspended(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.suspended_path)

    def _resumed_issue_numbers(self) -> Set[int]:
        return {
            item["escalation_issue_number"]
            for item in read_jsonl(self.resumed_path)
            if item.get("escalation_issue_number") is not None
        }


class SuspendedEventScanner:
    def __init__(self, queue: "QueueStore", client: Any, owner_login: str) -> None:
        self.queue = queue
        self.client = client
        self.owner_login = owner_login

    def scan_and_resume(self) -> List[Dict[str, Any]]:
        already_resumed = self.queue._resumed_issue_numbers()
        results: List[Dict[str, Any]] = []
        for record in self.queue.list_suspended():
            issue_number = record.get("escalation_issue_number")
            if issue_number is None or issue_number in already_resumed:
                continue
            event_dict = record.get("event", {})
            repo = event_dict.get("repo", "")
            if not repo:
                continue
            human_decision = self._get_human_decision(issue_number, repo)
            if human_decision is None:
                continue

            new_metadata = dict(event_dict.get("metadata", {}))
            new_metadata["human_decision"] = human_decision
            resumed_event_dict = {**event_dict, "metadata": new_metadata}

            enqueue_pending_payload(self.queue.runtime_dir, resumed_event_dict)
            append_jsonl(
                self.queue.resumed_path,
                {
                    "escalation_issue_number": issue_number,
                    "event_id": event_dict.get("event_id"),
                    "resumed_at": utc_now_iso(),
                    "human_decision": human_decision,
                },
            )
            results.append({"event_id": event_dict.get("event_id"), "issue_number": issue_number})
        return results

    def _get_human_decision(self, issue_number: int, repo: str) -> Optional[str]:
        if self.owner_login:
            comments_resp = self.client.api(f"repos/{repo}/issues/{issue_number}/comments", method="GET")
            if isinstance(comments_resp, list):
                for comment in comments_resp:
                    login = (comment.get("user") or {}).get("login", "")
                    if login == self.owner_login:
                        return comment.get("body") or ""
        issue_resp = self.client.api(f"repos/{repo}/issues/{issue_number}", method="GET")
        if isinstance(issue_resp, dict) and issue_resp.get("state") == "closed":
            return ""
        return None
