from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from github_pm_agent.models import ActionResult, Event
from github_pm_agent.utils import (
    append_jsonl,
    ensure_dir,
    load_text,
    parse_iso8601,
    read_json,
    read_jsonl,
    utc_now_iso,
    write_json,
)


class MemoryLoop:
    def __init__(self, runtime_dir: Path, config: Dict[str, Any]) -> None:
        self.runtime_dir = runtime_dir.resolve()
        self.project_root = Path(config.get("_project_root", runtime_dir.parent)).resolve()
        self.memory_dir = self.runtime_dir / "memory"
        self.raw_notes_path = self.runtime_dir / "memory_notes.jsonl"
        self.distilled_path = self.memory_dir / "distilled.md"
        self.policy_path = self.memory_dir / "policy.md"
        self.trend_path = self.memory_dir / "trends.md"
        self.retro_path = self.memory_dir / "retro.md"
        self.followups_path = self.runtime_dir / "followups.jsonl"
        self.state_path = self.memory_dir / "state.json"
        self.followup_state_path = self.memory_dir / "followup_state.json"
        ensure_dir(self.memory_dir)

        memory_config = config.get("engine", {}).get("memory", {})
        self.activity_batch_size = max(1, int(memory_config.get("activity_batch_size", 6)))
        self.min_notes_for_batch = max(1, int(memory_config.get("min_notes_for_batch", 2)))
        self.max_age_minutes = max(1, int(memory_config.get("max_age_minutes", 180)))
        self.lookback_notes = max(1, int(memory_config.get("lookback_notes", 48)))
        self.max_distilled_items = max(1, int(memory_config.get("max_distilled_items", 6)))
        self.retro_batch_size = max(1, int(memory_config.get("retro_batch_size", 10)))
        self.retro_max_age_minutes = max(1, int(memory_config.get("retro_max_age_minutes", 1440)))

    def memory_refs(self, base_refs: Iterable[str]) -> List[str]:
        refs = list(base_refs)
        for ref in (self.distilled_ref(), self.policy_ref(), self.trend_ref(), self.retro_ref()):
            if ref and ref not in refs:
                refs.append(ref)
        return refs

    def distilled_ref(self) -> Optional[str]:
        return self._ref_for(self.distilled_path)

    def policy_ref(self) -> Optional[str]:
        return self._ref_for(self.policy_path)

    def trend_ref(self) -> Optional[str]:
        return self._ref_for(self.trend_path)

    def retro_ref(self) -> Optional[str]:
        return self._ref_for(self.retro_path)

    def record_plan_result(self, event: Event, plan: Dict[str, Any], action_result: ActionResult) -> Optional[Dict[str, Any]]:
        note = self._plan_note(event, plan, action_result)
        payload = {
            "recorded_at": utc_now_iso(),
            "kind": "plan",
            "signal_kind": self._signal_kind_for_plan(event, plan, action_result),
            "event_id": event.event_id,
            "repo": event.repo,
            "event_type": event.event_type,
            "actor": event.actor,
            "target_kind": (plan.get("target") or {}).get("kind") or event.target_kind,
            "target_number": (plan.get("target") or {}).get("number") or event.target_number or 0,
            "action_type": action_result.action_type,
            "executed": action_result.executed,
            "summary": note,
            "needs_human_decision": bool(plan.get("needs_human_decision", False)),
            "follow_up_after_hours": self._coerce_non_negative_int(plan.get("follow_up_after_hours")),
            "urgency": self._coerce_text(plan.get("urgency"), default="normal"),
        }
        append_jsonl(self.raw_notes_path, payload)
        follow_up_after_hours = self._coerce_non_negative_int(plan.get("follow_up_after_hours"))
        if follow_up_after_hours > 0:
            self._record_followup(event, plan, follow_up_after_hours, payload)
        return payload

    def record_supervisor_note(self, note: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        cleaned = note.strip()
        if not cleaned:
            return None
        payload = {
            "recorded_at": utc_now_iso(),
            "kind": "supervisor",
            "signal_kind": "policy",
            "summary": cleaned,
        }
        if metadata:
            payload.update(metadata)
        append_jsonl(self.raw_notes_path, payload)
        return payload

    def note_activity(self, now_iso: Optional[str] = None) -> Dict[str, Any]:
        state = self._state()
        state["activities_since_synthesis"] = state.get("activities_since_synthesis", 0) + 1
        state["activities_since_retro"] = state.get("activities_since_retro", 0) + 1
        write_json(self.state_path, state)
        return self.maybe_synthesize(now_iso=now_iso, state=state)

    def maybe_synthesize(
        self,
        now_iso: Optional[str] = None,
        state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        state = state or self._state()
        notes = read_jsonl(self.raw_notes_path)
        pending = notes[state.get("last_note_index", 0) :]
        if not pending:
            return self._maybe_emit_retro(now_iso=now_iso, state=state)

        now_iso = now_iso or utc_now_iso()
        now = parse_iso8601(now_iso)
        oldest_pending = parse_iso8601(pending[0]["recorded_at"])
        age_trigger = now - oldest_pending >= timedelta(minutes=self.max_age_minutes)
        batch_trigger = (
            state.get("activities_since_synthesis", 0) >= self.activity_batch_size
            and len(pending) >= self.min_notes_for_batch
        )
        if not (age_trigger or batch_trigger):
            return self._maybe_emit_retro(now_iso=now_iso, state=state, reason="cadence-not-reached")

        self._write_memory_artifacts(notes)
        state["activities_since_synthesis"] = 0
        state["last_note_index"] = len(notes)
        state["last_synthesized_at"] = now_iso
        write_json(self.state_path, state)
        retro_result = self._maybe_emit_retro(now_iso=now_iso, state=state, reason="distilled")
        return {
            "refreshed": True,
            "reason": "age" if age_trigger and not batch_trigger else "batch",
            "retro": retro_result,
        }

    def _state(self) -> Dict[str, Any]:
        return read_json(
            self.state_path,
            {
                "activities_since_synthesis": 0,
                "activities_since_retro": 0,
                "last_note_index": 0,
                "last_synthesized_at": "",
                "last_retro_index": 0,
                "last_retro_at": "",
            },
        )

    def _coerce_text(self, value: Any, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _coerce_non_negative_int(self, value: Any) -> int:
        if value is None:
            return 0
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, number)

    def _coerce_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        if value is None:
            return default
        return bool(value)

    def _coerce_text_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        items: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                items.append(text)
        return items

    def due_followup_events(self, now_iso: Optional[str] = None) -> List[Event]:
        now_iso = now_iso or utc_now_iso()
        now = parse_iso8601(now_iso)
        records = read_jsonl(self.followups_path)
        state = self._followup_state()
        emitted_ids = set(state.get("emitted_ids", []))
        due_events: List[Event] = []
        for record in records:
            followup_id = str(record.get("followup_id", "")).strip()
            if not followup_id or followup_id in emitted_ids:
                continue
            due_at = str(record.get("due_at", "")).strip()
            if not due_at:
                continue
            if parse_iso8601(due_at) > now:
                continue
            due_events.append(
                Event(
                    event_id=followup_id,
                    event_type="follow_up_due",
                    source="memory_loop",
                    occurred_at=due_at,
                    repo=str(record.get("repo", "")),
                    actor="github-pm-agent",
                    url=str(record.get("source_url", "")),
                    title=str(record.get("title", "Follow-up due")),
                    body=str(record.get("summary", "") or record.get("reason", "")),
                    target_kind=str(record.get("target_kind", "none")),
                    target_number=record.get("target_number"),
                    metadata={
                        "followup_id": followup_id,
                        "source_event_id": record.get("event_id", ""),
                        "signal_kind": record.get("signal_kind", "policy"),
                        "due_at": due_at,
                        "summary": record.get("summary", ""),
                    },
                )
            )
            emitted_ids.add(followup_id)
        if due_events:
            self._write_followup_state(emitted_ids)
        return due_events

    def analytics_snapshot(self, now_iso: Optional[str] = None) -> Dict[str, Any]:
        now_iso = now_iso or utc_now_iso()
        notes = read_jsonl(self.raw_notes_path)
        signals: Dict[str, int] = defaultdict(int)
        kinds: Dict[str, int] = defaultdict(int)
        for note in notes:
            signal_kind = str(note.get("signal_kind", "")).strip() or "unknown"
            kind = str(note.get("kind", "")).strip() or "unknown"
            signals[signal_kind] += 1
            kinds[kind] += 1
        followups = read_jsonl(self.followups_path)
        emitted_ids = set(self._followup_state().get("emitted_ids", []))
        due_followups = 0
        pending_followups = 0
        for record in followups:
            followup_id = str(record.get("followup_id", "")).strip()
            due_at = str(record.get("due_at", "")).strip()
            if not followup_id or not due_at:
                continue
            if followup_id in emitted_ids:
                continue
            if parse_iso8601(due_at) <= parse_iso8601(now_iso):
                due_followups += 1
            else:
                pending_followups += 1
        return {
            "notes_total": len(notes),
            "signal_counts": dict(signals),
            "kind_counts": dict(kinds),
            "followup_counts": {
                "scheduled": len(followups),
                "due": due_followups,
                "pending": pending_followups,
                "emitted": len(emitted_ids),
            },
            "memory_files": {
                "distilled": self._ref_for(self.distilled_path),
                "policy": self._ref_for(self.policy_path),
                "trend": self._ref_for(self.trend_path),
                "retro": self._ref_for(self.retro_path),
            },
        }

    def _plan_note(self, event: Event, plan: Dict[str, Any], action_result: ActionResult) -> str:
        explicit = str(plan.get("memory_note", "")).strip()
        if explicit:
            return explicit

        if not action_result.executed:
            return ""

        action_type = action_result.action_type
        target = (plan.get("target") or {}).get("kind") or event.target_kind
        number = (plan.get("target") or {}).get("number") or event.target_number or 0
        if action_type == "issue":
            return f"follow-up issue created from {target} #{number}"

        if action_type == "label":
            labels_to_add = list(plan.get("labels_to_add", []))
            labels_to_remove = list(plan.get("labels_to_remove", []))
            fragments: List[str] = []
            if labels_to_add:
                fragments.append(f"added {', '.join(labels_to_add)}")
            if labels_to_remove:
                fragments.append(f"removed {', '.join(labels_to_remove)}")
            if fragments:
                return f"labels updated on {target} #{number}: {'; '.join(fragments)}"

        return ""

    def _ref_for(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        if not load_text(path).strip():
            return None
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(self.project_root))
        except ValueError:
            return str(resolved)

    def _followup_state(self) -> Dict[str, Any]:
        return read_json(self.followup_state_path, {"emitted_ids": []})

    def _write_followup_state(self, emitted_ids: Iterable[str]) -> None:
        payload = {"emitted_ids": sorted(set(emitted_ids))}
        write_json(self.followup_state_path, payload)

    def _record_followup(
        self,
        event: Event,
        plan: Dict[str, Any],
        follow_up_after_hours: int,
        note_payload: Dict[str, Any],
    ) -> None:
        target_kind = note_payload.get("target_kind") or event.target_kind
        target_number = note_payload.get("target_number") or event.target_number or 0
        due_at = (parse_iso8601(event.occurred_at) + timedelta(hours=follow_up_after_hours)).isoformat().replace("+00:00", "Z")
        followup_id = self._followup_id(event.event_id, target_kind, target_number, due_at)
        append_jsonl(
            self.followups_path,
            {
                "recorded_at": utc_now_iso(),
                "followup_id": followup_id,
                "repo": event.repo,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "source_url": event.url,
                "target_kind": target_kind,
                "target_number": target_number,
                "title": note_payload.get("summary") or plan.get("issue_title") or f"Follow-up for {event.title}",
                "summary": note_payload.get("summary") or plan.get("reason", ""),
                "reason": plan.get("reason", ""),
                "signal_kind": note_payload.get("signal_kind", "policy"),
                "due_at": due_at,
                "follow_up_after_hours": follow_up_after_hours,
            },
        )

    def _followup_id(self, event_id: str, target_kind: str, target_number: int, due_at: str) -> str:
        base = f"{event_id}:{target_kind}:{target_number}:{due_at}"
        return f"followup:{re.sub(r'[^a-zA-Z0-9]+', '-', base).strip('-')[:120]}"

    def _signal_kind_for_plan(self, event: Event, plan: Dict[str, Any], action_result: ActionResult) -> str:
        if bool(plan.get("needs_human_decision")) or self._coerce_non_negative_int(plan.get("follow_up_after_hours")) > 0:
            return "policy"
        if event.event_type in {
            "workflow_failed",
            "stale_pr_review",
            "blocked_issue_stale",
            "pull_request_review",
            "pull_request_review_comment",
            "commit",
        }:
            return "trend"
        if action_result.action_type in {"comment", "label", "issue", "assign", "review_request", "state"}:
            return "trend"
        return "policy"

    def _write_memory_artifacts(self, notes: Sequence[Dict[str, Any]]) -> None:
        combined = self._distill(notes, heading="# Distilled Memory", intro="Retain only durable repo patterns. Ignore one-off queue state.")
        policy = self._distill(
            notes,
            heading="# Repo Policy Memory",
            intro="Capture stable human-decision rules, scope boundaries, and escalation patterns.",
            signal_kind="policy",
        )
        trend = self._distill(
            notes,
            heading="# Execution Trend Memory",
            intro="Capture recurring operational signals and execution behavior.",
            signal_kind="trend",
        )
        retro = self._retro_summary(notes)
        self._write_text_if_changed(self.distilled_path, combined)
        self._write_text_if_changed(self.policy_path, policy)
        self._write_text_if_changed(self.trend_path, trend)
        self._write_text_if_changed(self.retro_path, retro)

    def _maybe_emit_retro(self, now_iso: Optional[str], state: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
        notes = read_jsonl(self.raw_notes_path)
        if not notes:
            return {"refreshed": False, "reason": reason or "no-pending-notes"}

        now_iso = now_iso or utc_now_iso()
        now = parse_iso8601(now_iso)
        last_retro_at = str(state.get("last_retro_at", "")).strip()
        last_retro_due = False
        if last_retro_at:
            last_retro_due = now - parse_iso8601(last_retro_at) >= timedelta(minutes=self.retro_max_age_minutes)
        retro_trigger = state.get("activities_since_retro", 0) >= self.retro_batch_size or last_retro_due
        if not retro_trigger:
            return {"refreshed": False, "reason": reason or "retro-not-due"}

        retro = self._retro_summary(notes)
        self._write_text_if_changed(self.retro_path, retro)
        state["activities_since_retro"] = 0
        state["last_retro_index"] = len(notes)
        state["last_retro_at"] = now_iso
        write_json(self.state_path, state)
        return {"refreshed": bool(retro), "reason": reason or "retro"}

    def _write_text_if_changed(self, path: Path, content: str) -> None:
        if not content.strip():
            return
        ensure_dir(path.parent)
        current = load_text(path)
        if current == content.rstrip() + "\n":
            return
        path.write_text(content.rstrip() + "\n", encoding="utf-8")

    def _distill(
        self,
        notes: Sequence[Dict[str, Any]],
        *,
        heading: str,
        intro: str,
        signal_kind: Optional[str] = None,
    ) -> str:
        window = list(notes[-self.lookback_notes :])
        if signal_kind is not None:
            window = [
                note
                for note in window
                if str(note.get("signal_kind", "")).strip() == signal_kind
                or (signal_kind == "policy" and note.get("kind") == "supervisor")
            ]
        category_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        repeated_note_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        supervisor_notes: List[str] = []

        for note in window:
            summary = str(note.get("summary", "")).strip()
            if not summary:
                continue
            if note.get("kind") == "supervisor":
                if summary not in supervisor_notes:
                    supervisor_notes.append(summary)
                continue

            category = self._category_for(note)
            if category:
                category_groups[category].append(note)
                continue

            repeated_note_groups[self._normalize_summary(summary)].append(note)

        candidates: List[Tuple[int, str]] = []
        for category, items in category_groups.items():
            line = self._render_category(category, items)
            if line:
                candidates.append((len(items), line))

        for items in repeated_note_groups.values():
            if len(items) < 2:
                continue
            summary = str(items[-1].get("summary", "")).strip()
            if not summary:
                continue
            candidates.append(
                (
                    len(items),
                    f"- Repeated operational signal: {len(items)} related notes recently matched "
                    f'"{self._trim(summary)}".',
                )
            )

        for note in supervisor_notes[:2]:
            candidates.append((2, f"- Supervisor signal: {self._trim(note)}"))

        if not candidates:
            return ""

        candidates.sort(key=lambda item: (-item[0], item[1]))
        lines = [heading, "", intro, ""]
        for _, line in candidates[: self.max_distilled_items]:
            lines.append(line)
        lines.append("")
        return "\n".join(lines)

    def _category_for(self, note: Dict[str, Any]) -> str:
        summary = str(note.get("summary", "")).lower()
        if str(note.get("signal_kind", "")).strip() == "policy":
            if note.get("needs_human_decision") or "decision" in summary or "scope" in summary:
                return "policy_decision"
            if note.get("follow_up_after_hours") or "follow-up" in summary:
                return "policy_follow_up"
            if "escalat" in summary or "owner" in summary:
                return "policy_escalation"
        if "changes requested" in summary:
            return "review_changes_requested"
        if "workflow failed" in summary or note.get("event_type") == "workflow_failed":
            return "workflow_failures"
        if "blocked" in summary:
            return "blocked_work"
        if "stale review reminder" in summary:
            return "stale_review_followup"
        if "follow-up issue created" in summary or note.get("action_type") == "issue":
            return "follow_up_issues"
        return ""

    def _render_category(self, category: str, items: Sequence[Dict[str, Any]]) -> str:
        if len(items) < 2:
            return ""
        count = len(items)
        examples = self._examples(items)
        if category == "policy_decision":
            return f"- Repo policy keeps requiring human decisions: {count} recent cases ({examples})."
        if category == "policy_follow_up":
            return f"- Policy follow-ups are recurring: {count} recent reminders ({examples})."
        if category == "policy_escalation":
            return f"- Escalation patterns are recurring: {count} recent records ({examples})."
        if category == "review_changes_requested":
            return f"- Review feedback is recurring: {count} recent PR review events needed changes ({examples})."
        if category == "workflow_failures":
            return f"- CI instability is showing up repeatedly: {count} workflow failure signals recently ({examples})."
        if category == "blocked_work":
            return f"- Blocked work keeps resurfacing: {count} recent blocked-work signals ({examples})."
        if category == "stale_review_followup":
            return f"- Review follow-up needs nudging: {count} stale-review reminders were posted ({examples})."
        if category == "follow_up_issues":
            return f"- The agent is creating follow-up issues repeatedly: {count} recent cases ({examples})."
        return ""

    def _retro_summary(self, notes: Sequence[Dict[str, Any]]) -> str:
        distilled = self._distill(
            notes,
            heading="# Retro Summary",
            intro="Summarize recurring patterns and the next operational adjustment.",
        )
        if not distilled:
            return ""
        lines = distilled.splitlines()
        if len(lines) > 2:
            lines.insert(3, "Use this to tighten policy, not to create new process noise.")
        return "\n".join(lines)

    def _examples(self, items: Sequence[Dict[str, Any]]) -> str:
        seen = set()
        labels: List[str] = []
        for item in reversed(items):
            label = self._target_label(item)
            if not label or label in seen:
                continue
            seen.add(label)
            labels.append(label)
            if len(labels) == 3:
                break
        return ", ".join(labels) if labels else "see recent notes"

    def _target_label(self, item: Dict[str, Any]) -> str:
        target_kind = str(item.get("target_kind", "")).strip()
        target_number = item.get("target_number") or 0
        if not target_kind or not target_number:
            return ""
        mapping = {
            "pull_request": "PR",
            "issue": "issue",
            "discussion": "discussion",
            "workflow_run": "workflow",
        }
        prefix = mapping.get(target_kind, target_kind.replace("_", " "))
        return f"{prefix} #{target_number}"

    def _normalize_summary(self, summary: str) -> str:
        normalized = summary.lower()
        normalized = re.sub(r"#\d+", "#n", normalized)
        normalized = re.sub(r"@\w+", "@user", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _trim(self, value: str, limit: int = 180) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."
