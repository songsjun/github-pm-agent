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
        self.state_path = self.memory_dir / "state.json"
        ensure_dir(self.memory_dir)

        memory_config = config.get("engine", {}).get("memory", {})
        self.activity_batch_size = max(1, int(memory_config.get("activity_batch_size", 6)))
        self.min_notes_for_batch = max(1, int(memory_config.get("min_notes_for_batch", 2)))
        self.max_age_minutes = max(1, int(memory_config.get("max_age_minutes", 180)))
        self.lookback_notes = max(1, int(memory_config.get("lookback_notes", 48)))
        self.max_distilled_items = max(1, int(memory_config.get("max_distilled_items", 6)))

    def memory_refs(self, base_refs: Iterable[str]) -> List[str]:
        refs = list(base_refs)
        distilled_ref = self.distilled_ref()
        if distilled_ref and distilled_ref not in refs:
            refs.append(distilled_ref)
        return refs

    def distilled_ref(self) -> Optional[str]:
        if not self.distilled_path.exists():
            return None
        if not load_text(self.distilled_path).strip():
            return None
        distilled_path = self.distilled_path.resolve()
        try:
            return str(distilled_path.relative_to(self.project_root))
        except ValueError:
            return str(distilled_path)

    def record_plan_result(self, event: Event, plan: Dict[str, Any], action_result: ActionResult) -> Optional[Dict[str, Any]]:
        note = self._plan_note(event, plan, action_result)
        if not note:
            return None
        payload = {
            "recorded_at": utc_now_iso(),
            "kind": "plan",
            "event_id": event.event_id,
            "repo": event.repo,
            "event_type": event.event_type,
            "actor": event.actor,
            "target_kind": (plan.get("target") or {}).get("kind") or event.target_kind,
            "target_number": (plan.get("target") or {}).get("number") or event.target_number or 0,
            "action_type": action_result.action_type,
            "executed": action_result.executed,
            "summary": note,
        }
        append_jsonl(self.raw_notes_path, payload)
        return payload

    def record_supervisor_note(self, note: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        cleaned = note.strip()
        if not cleaned:
            return None
        payload = {
            "recorded_at": utc_now_iso(),
            "kind": "supervisor",
            "summary": cleaned,
        }
        if metadata:
            payload.update(metadata)
        append_jsonl(self.raw_notes_path, payload)
        return payload

    def note_activity(self, now_iso: Optional[str] = None) -> Dict[str, Any]:
        state = self._state()
        state["activities_since_synthesis"] = state.get("activities_since_synthesis", 0) + 1
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
            return {"refreshed": False, "reason": "no-pending-notes"}

        now_iso = now_iso or utc_now_iso()
        now = parse_iso8601(now_iso)
        oldest_pending = parse_iso8601(pending[0]["recorded_at"])
        age_trigger = now - oldest_pending >= timedelta(minutes=self.max_age_minutes)
        batch_trigger = (
            state.get("activities_since_synthesis", 0) >= self.activity_batch_size
            and len(pending) >= self.min_notes_for_batch
        )
        if not (age_trigger or batch_trigger):
            return {"refreshed": False, "reason": "cadence-not-reached"}

        distilled = self._distill(notes)
        ensure_dir(self.distilled_path.parent)
        self.distilled_path.write_text(distilled, encoding="utf-8")
        state["activities_since_synthesis"] = 0
        state["last_note_index"] = len(notes)
        state["last_synthesized_at"] = now_iso
        write_json(self.state_path, state)
        return {"refreshed": True, "reason": "age" if age_trigger and not batch_trigger else "batch"}

    def _state(self) -> Dict[str, Any]:
        return read_json(
            self.state_path,
            {
                "activities_since_synthesis": 0,
                "last_note_index": 0,
                "last_synthesized_at": "",
            },
        )

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

    def _distill(self, notes: Sequence[Dict[str, Any]]) -> str:
        window = list(notes[-self.lookback_notes :])
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
        lines = [
            "# Distilled Memory",
            "",
            "Retain only durable repo patterns. Ignore one-off queue state.",
            "",
        ]
        for _, line in candidates[: self.max_distilled_items]:
            lines.append(line)
        lines.append("")
        return "\n".join(lines)

    def _category_for(self, note: Dict[str, Any]) -> str:
        summary = str(note.get("summary", "")).lower()
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
