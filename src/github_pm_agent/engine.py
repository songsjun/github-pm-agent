from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from github_pm_agent.handlers import resolve_handler
from github_pm_agent.models import ActionResult, AiRequest, Event
from github_pm_agent.utils import append_jsonl, ensure_dir, extract_json_object


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
        self.runtime_dir = runtime_dir
        self.memory_notes_path = runtime_dir / "memory_notes.jsonl"
        self.role_registry = None

    def process(self, event: Event) -> Dict[str, Any]:
        handler_name, handler = resolve_handler(self, event)
        result = handler(self, event)
        result["handler"] = handler_name
        return result

    def run_ai_handler(
        self,
        event: Event,
        prompt_path: str,
        memory_refs: Optional[Iterable[str]] = None,
        skill_refs: Optional[Iterable[str]] = None,
        role: str = "pm",
        variables: Optional[Dict[str, Any]] = None,
        output_template_path: Optional[str] = "templates/output/action_plan.json",
        output_schema_path: Optional[str] = "templates/output/action_plan.schema.json",
        session_key_suffix: str = "",
    ) -> Dict[str, Any]:
        request = self._build_ai_request(
            event,
            prompt_path=prompt_path,
            memory_refs=memory_refs,
            skill_refs=skill_refs,
            role=role,
            variables=variables,
            output_template_path=output_template_path,
            output_schema_path=output_schema_path,
            session_key_suffix=session_key_suffix,
        )
        response = self.ai_manager.generate(request)
        plan = self.parse_action_plan(response.content)
        result = self.finish_plan(event, plan)
        if self.config.get("engine", {}).get("supervisor_enabled", False):
            self._run_supervisor(request, response)
        self._attach_ai_metadata(result, response)
        return result

    def run_raw_text_handler(
        self,
        event: Event,
        prompt_path: str,
        role: str = "pm",
        variables: Optional[Dict[str, Any]] = None,
        session_key_suffix: str = "",
    ) -> Dict[str, Any]:
        """Run AI and return raw text output without JSON parsing or action execution."""
        request = self._build_ai_request(
            event,
            prompt_path=prompt_path,
            role=role,
            variables=variables,
            output_template_path=None,
            output_schema_path=None,
            session_key_suffix=session_key_suffix,
        )
        response = self.ai_manager.generate(request)
        result: Dict[str, Any] = {
            "raw_text": response.content,
            "action": {
                "executed": False,
                "action_type": "none",
                "target": {"kind": event.target_kind, "number": event.target_number or 0},
                "message": "",
                "raw": {},
            },
        }
        self._attach_ai_metadata(result, response)
        return result

    def run_veto_handler(self, event: Event, role: str = "pm") -> Dict[str, Any]:
        prompt_path, extra_variables = self._resolve_veto_prompt(event)
        request = self._build_ai_request(
            event,
            prompt_path=prompt_path,
            role=role,
            variables=extra_variables,
            output_template_path=None,
            output_schema_path=None,
            session_key_suffix="veto",
        )
        response = self.ai_manager.generate(request)

        parsed = extract_json_object(response.content)
        vetoed = False
        veto_reason = ""
        plan_reason = "veto check completed"
        if isinstance(parsed, dict):
            raw_should_block = parsed.get("should_block")
            if isinstance(raw_should_block, bool):
                vetoed = raw_should_block
            elif isinstance(raw_should_block, str):
                vetoed = raw_should_block.strip().lower() == "true"
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
        if self.config.get("engine", {}).get("supervisor_enabled", False):
            self._run_supervisor(request, response)
        self._attach_ai_metadata(result, response)
        return result

    def parse_action_plan(self, content: str) -> Dict[str, Any]:
        parsed = extract_json_object(content)
        if isinstance(parsed, dict):
            return parsed
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
        memory_note: str = "",
        issue_title: str = "",
    ) -> Dict[str, Any]:
        return {
            "should_act": should_act,
            "reason": reason,
            "action_type": action_type,
            "target": {"kind": target_kind, "number": target_number or 0},
            "message": message,
            "labels_to_add": list(labels_to_add or []),
            "labels_to_remove": list(labels_to_remove or []),
            "memory_note": memory_note,
            "issue_title": issue_title,
        }

    def finish_plan(self, event: Event, plan: Dict[str, Any]) -> Dict[str, Any]:
        result = self._execute_plan(event, plan)
        return {
            "plan": plan,
            "action": {
                "executed": result.executed,
                "action_type": result.action_type,
                "target": result.target,
                "message": result.message,
                "raw": result.raw,
            },
        }

    def _build_ai_request(
        self,
        event: Event,
        *,
        prompt_path: str,
        memory_refs: Optional[Iterable[str]] = None,
        skill_refs: Optional[Iterable[str]] = None,
        role: str = "pm",
        variables: Optional[Dict[str, Any]] = None,
        output_template_path: Optional[str] = "templates/output/action_plan.json",
        output_schema_path: Optional[str] = "templates/output/action_plan.schema.json",
        session_key_suffix: str = "",
    ) -> AiRequest:
        role_config = self.role_registry.load(role) if self.role_registry else {}
        provider = self.ai_manager.default_provider()
        model = self.ai_manager.default_model(provider)
        request_variables = {
            "repo": event.repo,
            "event_type": event.event_type,
            "event_payload": json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
        }
        request_variables.update(variables or {})
        session_key = f"{role}::{event.repo.replace('/', '__')}__{event.target_kind}__{event.target_number or event.event_id}"
        if session_key_suffix:
            session_key = f"{session_key}::{session_key_suffix}"
        return AiRequest(
            provider=provider,
            model=model,
            system_prompt_path=role_config.get("system_prompt_path", "prompts/system/pm.md"),
            prompt_path=prompt_path,
            variables=request_variables,
            memory_refs=list(memory_refs or ["memory/README.md"]),
            skill_refs=list(skill_refs or role_config.get("skill_refs", ["skills/pm-core.md"])),
            output_template_path=output_template_path,
            output_schema_path=output_schema_path,
            session_key=session_key,
        )

    def _resolve_veto_prompt(self, event: Event) -> Tuple[str, Dict[str, str]]:
        project_root = getattr(self.ai_manager, "project_root", None)
        if project_root:
            prompt_path = Path(project_root) / "prompts" / "actions" / "veto_check.md"
            if prompt_path.exists():
                return "prompts/actions/veto_check.md", {}

        inline_prompt_path = Path(self.runtime_dir) / "_inline_prompts" / "veto_check.md"
        ensure_dir(inline_prompt_path.parent)
        if not inline_prompt_path.exists():
            inline_prompt_path.write_text("${inline_prompt}\n", encoding="utf-8")
        return str(inline_prompt_path), {"inline_prompt": self._build_inline_veto_prompt(event)}

    def _build_inline_veto_prompt(self, event: Event) -> str:
        return (
            f"Event type: {event.event_type}\n"
            f"Repository: {event.repo}\n\n"
            "Task:\n"
            "Given this GitHub event, decide if work should be blocked.\n"
            "Only block when there is a concrete reason in the event details.\n"
            "If there is not enough evidence to block, return false.\n\n"
            "Event payload:\n"
            f"{json.dumps(event.to_dict(), indent=2, ensure_ascii=False)}\n\n"
            'Output exactly JSON: {"should_block": true/false, "reason": "..."}\n'
        )

    def _attach_ai_metadata(self, result: Dict[str, Any], response: Any) -> None:
        result["ai"] = {
            "provider": response.provider,
            "model": response.model,
            "session_key": response.session_key,
        }

    def _execute_plan(self, event: Event, plan: Dict[str, Any]) -> ActionResult:
        action_type = plan.get("action_type", "none")
        target = plan.get("target") or {}
        target_kind = target.get("kind") or event.target_kind
        target_number = target.get("number") or event.target_number
        message = plan.get("message", "")
        raw: Dict[str, Any] = {}

        if not plan.get("should_act"):
            if plan.get("memory_note"):
                append_jsonl(self.memory_notes_path, {"event_id": event.event_id, "note": plan["memory_note"]})
            return ActionResult(False, "none", target, message, raw)

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
        else:
            raw = {"note": "no executable action mapped", "message": message}

        if plan.get("memory_note"):
            append_jsonl(self.memory_notes_path, {"event_id": event.event_id, "note": plan["memory_note"]})

        return ActionResult(True, action_type, target, message, raw)

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
            append_jsonl(self.memory_notes_path, {"type": "supervisor", "note": note})
