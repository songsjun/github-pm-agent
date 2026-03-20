from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from github_pm_agent.models import Event
from github_pm_agent.utils import append_jsonl, read_json, read_jsonl, utc_now_iso, write_json, write_jsonl


class QueueStore:
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

    def mark_suspended(
        self,
        event: Event,
        escalation_issue_number: Optional[int],
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
    def __init__(self, queue: QueueStore, client: Any, owner_login: str) -> None:
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

            append_jsonl(self.queue.pending_path, resumed_event_dict)
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
