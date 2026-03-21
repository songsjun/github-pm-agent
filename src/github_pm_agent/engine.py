from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from github_pm_agent.artifact_store import ArtifactStore
from github_pm_agent.handlers import resolve_handler
from github_pm_agent.memory_loop import MemoryLoop
from github_pm_agent.models import ActionResult, AiRequest, Event
from github_pm_agent.utils import extract_json_object, utc_now_iso

ESCALATION_URGENCY_LEVELS = {"low", "normal", "high", "urgent"}
PROMPT_READ_ARTIFACT_KINDS = {
    "prompts/actions/intake_clarify.md": ("brief",),
    "prompts/actions/spec_review.md": ("brief", "spec_review"),
    "prompts/actions/blocker_investigation.md": ("brief", "spec_review"),
    "prompts/actions/review_readiness.md": ("brief", "spec_review"),
    "prompts/actions/release_readiness.md": ("brief", "spec_review", "release_readiness"),
    "prompts/actions/retro_summary.md": ("brief", "spec_review", "release_readiness", "retro_summary"),
}
PROMPT_WRITE_ARTIFACT_KIND = {
    "prompts/actions/intake_clarify.md": "brief",
    "prompts/actions/spec_review.md": "spec_review",
    "prompts/actions/release_readiness.md": "release_readiness",
    "prompts/actions/retro_summary.md": "retro_summary",
}


class EventEngine:
    def __init__(
        self,
        config: Dict[str, Any],
        ai_manager: Any,
        actions: Any,
        runtime_dir: Any,
    ) -> None:
        self.config = config
        self.ai_manager = ai_manager
        self.actions = actions
        self.runtime_dir = Path(runtime_dir).resolve()
        self.project_root = Path(config.get("_project_root", self.runtime_dir.parent)).resolve()
        self.memory_loop = MemoryLoop(runtime_dir, config)
        self._attach_memory_loop_compatibility()
        self.artifacts = ArtifactStore(self.runtime_dir, project_root=self.project_root)
        self.role_registry = None

    def process(self, event: Event) -> Dict[str, Any]:
        handler_name, handler = resolve_handler(self, event)
        result = handler(self, event)
        result["handler"] = handler_name
        self.memory_loop.note_activity()
        return result

    def _attach_memory_loop_compatibility(self) -> None:
        if not hasattr(self.memory_loop, "_coerce_non_negative_int"):
            setattr(self.memory_loop, "_coerce_non_negative_int", self._coerce_non_negative_int)
        if not hasattr(self.memory_loop, "_coerce_text"):
            setattr(self.memory_loop, "_coerce_text", self._coerce_text)

    def run_ai_handler(
        self,
        event: Event,
        prompt_path: str,
        memory_refs: Optional[Iterable[str]] = None,
        skill_refs: Optional[Iterable[str]] = None,
        risk_level: str = "normal",
        requires_human: bool = False,
    ) -> Dict[str, Any]:
        provider = self.ai_manager.default_provider()
        model = self.ai_manager.default_model(provider)
        artifact_refs = self.artifacts.latest_refs(self._artifact_kinds_for_prompt(prompt_path))
        request = AiRequest(
            provider=provider,
            model=model,
            system_prompt_path="prompts/system/pm.md",
            prompt_path=prompt_path,
            variables={
                "repo": event.repo,
                "event_type": event.event_type,
                "event_payload": json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
            },
            memory_refs=self.memory_loop.memory_refs(memory_refs or ["memory/README.md"]),
            skill_refs=list(skill_refs or ["skills/pm-core.md"]),
            artifact_refs=artifact_refs,
            output_template_path="templates/output/action_plan.json",
            output_schema_path="templates/output/action_plan.schema.json",
            session_key=f"{event.repo.replace('/', '__')}__{event.target_kind}__{event.target_number or event.event_id}",
        )
        response = self.ai_manager.generate(request)
        plan = self.parse_action_plan(response.content)
        result = self.finish_plan(event, plan)
        artifact_record = self._maybe_record_artifact(event, prompt_path, result["plan"])
        if artifact_record:
            result["artifact"] = artifact_record.to_dict()
        second_opinion = self._maybe_run_second_opinion(
            event,
            prompt_path=prompt_path,
            primary_plan=result["plan"],
            memory_refs=request.memory_refs,
            skill_refs=request.skill_refs,
            artifact_refs=artifact_refs,
            risk_level=risk_level,
            requires_human=requires_human,
        )
        if second_opinion:
            result["second_opinion"] = second_opinion
        if self.config.get("engine", {}).get("supervisor_enabled", False):
            self._run_supervisor(request, response)
        result["ai"] = {
            "provider": response.provider,
            "model": response.model,
            "session_key": response.session_key,
        }
        return result

    def run_raw_text_handler(
        self,
        event: Event,
        prompt_path: str,
        role: str = "pm",
        variables: Optional[Dict[str, Any]] = None,
        session_key_suffix: str = "",
    ) -> Dict[str, Any]:
        """Run an AI prompt and return the raw text response without JSON parsing or action execution."""
        provider = self.ai_manager.default_provider()
        model = self.ai_manager.default_model(provider)
        base_variables: Dict[str, Any] = {
            "repo": event.repo,
            "event_type": event.event_type,
            "event_payload": json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
        }
        if variables:
            base_variables.update(variables)
        session_key = f"{role}::{event.repo.replace('/', '__')}__{event.target_kind}__{event.target_number or event.event_id}"
        if session_key_suffix:
            session_key += f"__{session_key_suffix}"
        request = AiRequest(
            provider=provider,
            model=model,
            system_prompt_path=f"prompts/system/{role}.md",
            prompt_path=prompt_path,
            variables=base_variables,
            memory_refs=[],
            skill_refs=[],
            artifact_refs=[],
            output_template_path=None,
            output_schema_path=None,
            session_key=session_key,
        )
        response = self.ai_manager.generate(request)
        if request.session_key:
            self.ai_manager.session_store.append_turn(
                request.session_key,
                json.dumps(base_variables, ensure_ascii=False),
                response.content,
            )
        return {
            "raw_text": response.content,
            "action": {"executed": False, "action_type": "none"},
            "ai": {
                "provider": response.provider,
                "model": response.model,
                "session_key": response.session_key,
            },
        }

    def run_veto_handler(self, event: Event, role: str = "pm") -> Dict[str, Any]:
        """Run a veto check and return the result."""
        provider = self.ai_manager.default_provider()
        model = self.ai_manager.default_model(provider)
        request = AiRequest(
            provider=provider,
            model=model,
            system_prompt_path=f"prompts/system/{role}.md",
            prompt_path="prompts/actions/veto_check.md",
            variables={
                "repo": event.repo,
                "event_type": event.event_type,
                "event_payload": json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
            },
            memory_refs=[],
            skill_refs=[],
            artifact_refs=[],
            output_template_path=None,
            output_schema_path=None,
            session_key=f"{role}::{event.repo.replace('/', '__')}__{event.target_kind}__{event.target_number or event.event_id}__veto",
        )
        response = self.ai_manager.generate(request)
        parsed = extract_json_object(response.content)
        vetoed = False
        veto_reason = ""
        plan_reason = "veto check completed"
        if isinstance(parsed, dict):
            raw = parsed.get("should_block")
            if isinstance(raw, bool):
                vetoed = raw
            elif isinstance(raw, str):
                vetoed = raw.strip().lower() == "true"
            veto_reason = str(parsed.get("reason") or "").strip()
        else:
            plan_reason = "model output was not valid JSON; veto check failed open"
        plan = self.make_plan(
            should_act=False,
            reason=plan_reason,
            action_type="none",
            target_kind=event.target_kind,
            target_number=event.target_number,
            message=veto_reason,
        )
        result = self.finish_plan(event, plan)
        result["handler"] = "veto"
        result["vetoed"] = vetoed
        result["veto_reason"] = veto_reason
        result["veto"] = {"should_block": vetoed, "reason": veto_reason}
        result["ai"] = {"provider": response.provider, "model": response.model, "session_key": response.session_key}
        return result

    def parse_action_plan(self, content: str) -> Dict[str, Any]:
        parsed = extract_json_object(content)
        if isinstance(parsed, dict):
            return self._normalize_plan(parsed)
        return self.make_plan(
            should_act=False,
            reason="model output was not valid JSON; stored as observation only",
            action_type="none",
            target_kind="none",
            target_number=0,
            message=content,
        )

    def make_plan(
        self,
        *,
        should_act: bool,
        reason: str,
        action_type: str,
        target_kind: str,
        target_number: Optional[int],
        message: str,
        labels_to_add: Optional[Iterable[str]] = None,
        labels_to_remove: Optional[Iterable[str]] = None,
        action_input: Optional[Dict[str, Any]] = None,
        memory_note: str = "",
        issue_title: str = "",
        needs_human_decision: bool = False,
        human_decision_reason: str = "",
        urgency: str = "normal",
        follow_up_after_hours: int = 0,
        evidence: Optional[Iterable[str]] = None,
        options: Optional[Iterable[Any]] = None,
    ) -> Dict[str, Any]:
        return self._normalize_plan(
            {
                "should_act": should_act,
                "reason": reason,
                "action_type": action_type,
                "target": {"kind": target_kind, "number": target_number or 0},
                "message": message,
                "labels_to_add": list(labels_to_add or []),
                "labels_to_remove": list(labels_to_remove or []),
                "action_input": dict(action_input or {}),
                "memory_note": memory_note,
                "issue_title": issue_title,
                "needs_human_decision": needs_human_decision,
                "human_decision_reason": human_decision_reason,
                "urgency": urgency,
                "follow_up_after_hours": follow_up_after_hours,
                "evidence": list(evidence or []),
                "options": list(options or []),
            }
        )

    def finish_plan(self, event: Event, plan: Dict[str, Any]) -> Dict[str, Any]:
        normalized_plan = self._normalize_plan(plan)
        result = self._execute_plan(event, normalized_plan)
        return {
            "plan": normalized_plan,
            "escalation": self._escalation_view(normalized_plan),
            "action": {
                "executed": result.executed,
                "action_type": result.action_type,
                "target": result.target,
                "message": result.message,
                "raw": result.raw,
            },
        }

    def _normalize_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(plan or {}) if isinstance(plan, dict) else {}
        target = normalized.get("target")
        target = target if isinstance(target, dict) else {}
        action_input = normalized.get("action_input")
        action_input = action_input if isinstance(action_input, dict) else {}
        normalized["should_act"] = self._coerce_bool(normalized.get("should_act"), default=False)
        normalized["reason"] = self._coerce_text(normalized.get("reason"))
        normalized["action_type"] = self._coerce_text(normalized.get("action_type"), default="none")
        normalized["target"] = {
            "kind": self._coerce_text(target.get("kind"), default="none"),
            "number": self._coerce_non_negative_int(target.get("number")),
        }
        normalized["message"] = self._coerce_text(normalized.get("message"))
        normalized["labels_to_add"] = self._coerce_text_list(normalized.get("labels_to_add"))
        normalized["labels_to_remove"] = self._coerce_text_list(normalized.get("labels_to_remove"))
        normalized["action_input"] = dict(action_input)
        normalized["memory_note"] = self._coerce_text(normalized.get("memory_note"))
        normalized["issue_title"] = self._coerce_text(normalized.get("issue_title"))
        normalized["needs_human_decision"] = self._coerce_bool(
            normalized.get("needs_human_decision"),
            default=False,
        )
        normalized["human_decision_reason"] = self._coerce_text(normalized.get("human_decision_reason"))
        normalized["urgency"] = self._normalize_urgency(normalized.get("urgency"))
        normalized["follow_up_after_hours"] = self._coerce_non_negative_int(normalized.get("follow_up_after_hours"))
        normalized["evidence"] = self._coerce_text_list(normalized.get("evidence"))
        normalized["options"] = self._coerce_options(normalized.get("options"))
        return normalized

    def _coerce_text(self, value: Any, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

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

    def _coerce_non_negative_int(self, value: Any) -> int:
        if value is None:
            return 0
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, number)

    def _coerce_text_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                items.append(text)
        return items

    def _coerce_options(self, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        options: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                options.append(dict(item))
                continue
            if item is None:
                continue
            text = str(item).strip()
            if text:
                options.append(text)
        return options

    def _normalize_urgency(self, value: Any) -> str:
        urgency = self._coerce_text(value, default="normal").lower()
        return urgency if urgency in ESCALATION_URGENCY_LEVELS else "normal"

    def _escalation_view(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "needs_human_decision": plan.get("needs_human_decision", False),
            "human_decision_reason": plan.get("human_decision_reason", ""),
            "urgency": plan.get("urgency", "normal"),
            "follow_up_after_hours": plan.get("follow_up_after_hours", 0),
            "evidence": list(plan.get("evidence", [])),
            "options": list(plan.get("options", [])),
        }

    def _artifact_kinds_for_prompt(self, prompt_path: str) -> tuple[str, ...]:
        return PROMPT_READ_ARTIFACT_KINDS.get(prompt_path, ())

    def _maybe_record_artifact(
        self,
        event: Event,
        prompt_path: str,
        plan: Dict[str, Any],
    ) -> Any:
        kind = PROMPT_WRITE_ARTIFACT_KIND.get(prompt_path)
        if not kind:
            return None
        body = self._render_artifact_body(event, plan)
        if not body.strip():
            return None
        return self.artifacts.save(
            kind,
            title=self._artifact_title(kind, event),
            summary=plan.get("reason", ""),
            body=body,
            metadata={
                "repo": event.repo,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "target_kind": event.target_kind,
                "target_number": event.target_number or 0,
                "prompt_path": prompt_path,
                "needs_human_decision": plan.get("needs_human_decision", False),
                "urgency": plan.get("urgency", "normal"),
            },
            created_at=utc_now_iso(),
        )

    def _artifact_title(self, kind: str, event: Event) -> str:
        label = kind.replace("_", " ").title()
        if event.target_number:
            return f"{label}: {event.target_kind} #{event.target_number}"
        if event.title:
            return f"{label}: {event.title}"
        return label

    def _render_artifact_body(self, event: Event, plan: Dict[str, Any]) -> str:
        lines = [
            f"Source repo: `{event.repo}`",
            f"Source event: `{event.event_type}`",
            f"Target: `{event.target_kind}` #{event.target_number or 0}",
        ]
        if event.url:
            lines.append(f"Source URL: {event.url}")
        if event.title:
            lines.extend(["", "## Event Title", event.title])
        if plan.get("reason"):
            lines.extend(["", "## Assessment", plan["reason"]])
        if plan.get("needs_human_decision"):
            lines.extend(
                [
                    "",
                    "## Human Decision Needed",
                    plan.get("human_decision_reason") or "The model flagged this for human review.",
                ]
            )
        evidence = list(plan.get("evidence", []))
        if evidence:
            lines.extend(["", "## Evidence"])
            lines.extend(f"- {item}" for item in evidence)
        options = list(plan.get("options", []))
        if options:
            lines.extend(["", "## Options"])
            for item in options:
                if isinstance(item, dict):
                    label = str(item.get("label") or item.get("name") or "option").strip()
                    summary = str(item.get("summary") or item.get("description") or "").strip()
                    lines.append(f"- {label}: {summary}" if summary else f"- {label}")
                else:
                    lines.append(f"- {item}")
        if plan.get("message"):
            lines.extend(["", "## Suggested Output", plan["message"]])
        follow_up_after_hours = plan.get("follow_up_after_hours", 0)
        if follow_up_after_hours:
            lines.extend(["", "## Follow-up", f"- Re-check in about {follow_up_after_hours} hours."])
        return "\n".join(lines).strip()

    def _execute_plan(self, event: Event, plan: Dict[str, Any]) -> ActionResult:
        action_type = plan.get("action_type", "none")
        target = plan.get("target") or {}
        target_kind = target.get("kind") or event.target_kind
        target_number = target.get("number") or event.target_number
        message = plan.get("message", "")
        action_input = plan.get("action_input") or {}
        raw: Dict[str, Any] = {}

        if not plan.get("should_act"):
            result = ActionResult(False, "none", target, message, raw)
            self.memory_loop.record_plan_result(event, plan, result)
            return result

        if action_type == "comment" and target_number:
            if target_kind == "discussion":
                discussion_id = event.metadata.get("discussion_node_id") or event.metadata.get("node_id")
                if discussion_id:
                    raw = self.actions.comment_on_discussion(discussion_id, target_number, message)
                else:
                    raw = {"note": "missing discussion node id", "message": message}
            else:
                raw = self.actions.comment(target_kind, target_number, message)
        elif action_type == "label" and target_number:
            added = self.actions.add_labels(target_number, plan.get("labels_to_add", []))
            removed = self.actions.remove_labels(target_number, plan.get("labels_to_remove", []))
            raw = {"add": added, "remove": removed}
        elif action_type == "issue":
            raw = self.actions.create_issue(
                title=plan.get("issue_title", f"Follow-up for {event.title}"),
                body=message,
                labels=plan.get("labels_to_add", []),
            )
        elif action_type == "assign" and target_number:
            raw = self.actions.assign(target_kind, target_number, action_input.get("users", []))
        elif action_type == "unassign" and target_number:
            raw = self.actions.unassign(target_kind, target_number, action_input.get("users", []))
        elif action_type == "review_request" and target_number:
            raw = self.actions.request_review(target_number, action_input.get("users", []))
        elif action_type == "remove_reviewer" and target_number:
            raw = self.actions.remove_reviewers(target_number, action_input.get("reviewers", []))
        elif action_type == "edit" and target_number:
            raw = self.actions.edit(
                target_kind,
                target_number,
                {
                    key: action_input.get(key)
                    for key in ("title", "body")
                    if action_input.get(key) is not None
                },
            )
        elif action_type == "milestone" and target_number:
            raw = self.actions.set_milestone(target_kind, target_number, action_input.get("milestone"))
        elif action_type == "draft" and target_number:
            raw = self.actions.mark_pull_request_draft(target_number)
        elif action_type == "ready_for_review" and target_number:
            raw = self.actions.mark_pull_request_ready(target_number)
        elif action_type == "merge" and target_number:
            raw = self.actions.merge_pull_request(
                target_number,
                {
                    key: action_input.get(key)
                    for key in ("merge_method", "commit_title", "commit_message", "sha")
                    if action_input.get(key) is not None
                },
            )
        elif action_type == "review_decision" and target_number:
            raw = self.actions.submit_review_decision(
                target_number,
                action_input.get("decision", ""),
                body=action_input.get("body", ""),
                commit_id=action_input.get("commit_id", ""),
            )
        elif action_type == "rerun_workflow":
            raw = self.actions.rerun_workflow(action_input.get("run_id") or target_number)
        elif action_type == "cancel_workflow":
            raw = self.actions.cancel_workflow(action_input.get("run_id") or target_number)
        elif action_type == "create_release":
            raw = self.actions.create_release(**action_input)
        elif action_type == "create_discussion":
            raw = self.actions.create_discussion(
                action_input.get("repository_id", ""),
                action_input.get("category_id", ""),
                action_input.get("title", ""),
                action_input.get("body", ""),
            )
        elif action_type == "update_discussion":
            raw = self.actions.update_discussion(
                action_input.get("discussion_id", ""),
                title=action_input.get("title", ""),
                body=action_input.get("body", ""),
                category_id=action_input.get("category_id", ""),
            )
        elif action_type == "project":
            raw = self.actions.update_project_field(
                action_input.get("project_id", ""),
                action_input.get("item_id", ""),
                action_input.get("field_id", ""),
                action_input.get("value", {}),
            )
        elif action_type == "state" and target_number:
            raw = self.actions.set_state(target_kind, target_number, action_input.get("state", ""))
        else:
            raw = {"note": "no executable action mapped", "message": message}

        result = ActionResult(True, action_type, target, message, raw)
        self.memory_loop.record_plan_result(event, plan, result)
        return result

    def _maybe_run_second_opinion(
        self,
        event: Event,
        *,
        prompt_path: str,
        primary_plan: Dict[str, Any],
        memory_refs: Iterable[str],
        skill_refs: Iterable[str],
        artifact_refs: Iterable[str],
        risk_level: str,
        requires_human: bool,
    ) -> Optional[Dict[str, Any]]:
        second_opinion = self.config.get("engine", {}).get("second_opinion", {})
        if not second_opinion.get("enabled", False):
            return None
        if event.target_kind != "pull_request":
            return None
        if risk_level not in {"high", "urgent"} and not requires_human:
            return None

        provider = second_opinion.get("provider") or self.ai_manager.default_provider()
        model = second_opinion.get("model") or self.ai_manager.default_model(provider)
        request = AiRequest(
            provider=provider,
            model=model,
            system_prompt_path="prompts/system/pm.md",
            prompt_path="prompts/actions/second_opinion_review.md",
            variables={
                "repo": event.repo,
                "event_type": event.event_type,
                "event_payload": json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
                "primary_plan": json.dumps(primary_plan, indent=2, ensure_ascii=False),
                "route": json.dumps(
                    {
                        "risk_level": risk_level,
                        "requires_human": requires_human,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            },
            memory_refs=list(memory_refs),
            skill_refs=list(skill_refs),
            artifact_refs=list(artifact_refs),
            output_template_path="templates/output/action_plan.json",
            output_schema_path="templates/output/action_plan.schema.json",
            session_key=f"{event.repo.replace('/', '__')}__{event.target_kind}__{event.target_number or event.event_id}__second_opinion",
        )
        response = self.ai_manager.generate(request)
        return {
            "provider": response.provider,
            "model": response.model,
            "session_key": response.session_key,
            "plan": self.parse_action_plan(response.content),
        }

    def _run_supervisor(self, request: AiRequest, response: Any) -> None:
        provider = self.ai_manager.default_provider()
        model = self.ai_manager.default_model(provider)
        supervisor_request = AiRequest(
            provider=provider,
            model=model,
            system_prompt_path="prompts/system/pm.md",
            prompt_path="prompts/supervisor/review.md",
            variables={
                "request": json.dumps(
                    {
                        "prompt_path": request.prompt_path,
                        "provider": request.provider,
                        "model": request.model,
                        "variables": request.variables,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "response": response.content,
            },
            output_template_path="templates/output/supervisor_note.json",
            output_schema_path="templates/output/supervisor_note.schema.json",
            session_key=None,
        )
        supervisor_response = self.ai_manager.generate(supervisor_request)
        note = supervisor_response.content.strip()
        if note:
            self.memory_loop.record_supervisor_note(
                note,
                metadata={
                    "repo": request.variables.get("repo", ""),
                    "prompt_path": request.prompt_path,
                },
            )
