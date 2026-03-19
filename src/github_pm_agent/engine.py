from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from github_pm_agent.handlers import resolve_handler
from github_pm_agent.memory_loop import MemoryLoop
from github_pm_agent.models import ActionResult, AiRequest, Event
from github_pm_agent.utils import extract_json_object


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
        self.memory_loop = MemoryLoop(runtime_dir, config)

    def process(self, event: Event) -> Dict[str, Any]:
        handler_name, handler = resolve_handler(self, event)
        result = handler(self, event)
        result["handler"] = handler_name
        self.memory_loop.note_activity()
        return result

    def run_ai_handler(
        self,
        event: Event,
        prompt_path: str,
        memory_refs: Optional[Iterable[str]] = None,
        skill_refs: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        provider = self.ai_manager.default_provider()
        model = self.ai_manager.default_model(provider)
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
            output_template_path="templates/output/action_plan.json",
            output_schema_path="templates/output/action_plan.schema.json",
            session_key=f"{event.repo.replace('/', '__')}__{event.target_kind}__{event.target_number or event.event_id}",
        )
        response = self.ai_manager.generate(request)
        plan = self.parse_action_plan(response.content)
        result = self.finish_plan(event, plan)
        if self.config.get("engine", {}).get("supervisor_enabled", False):
            self._run_supervisor(request, response)
        result["ai"] = {
            "provider": response.provider,
            "model": response.model,
            "session_key": response.session_key,
        }
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
        action_input: Optional[Dict[str, Any]] = None,
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
            "action_input": dict(action_input or {}),
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
        elif action_type == "review_request" and target_number:
            raw = self.actions.request_review(target_number, action_input.get("users", []))
        elif action_type == "state" and target_number:
            raw = self.actions.set_state(target_kind, target_number, action_input.get("state", ""))
        else:
            raw = {"note": "no executable action mapped", "message": message}

        result = ActionResult(True, action_type, target, message, raw)
        self.memory_loop.record_plan_result(event, plan, result)
        return result

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
