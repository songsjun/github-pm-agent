from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class _NoOpActions:
    def _skip(self, action_type: str, **extra: Any) -> Dict[str, Any]:
        return {"action_type": action_type, "skipped": True, "dry_run": True, **extra}

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        return self._skip(
            "comment",
            target_kind=target_kind,
            target_number=target_number,
            message=message,
        )

    def comment_on_discussion(self, discussion_id: str, number: Optional[int], message: str) -> Dict[str, Any]:
        return self._skip(
            "comment",
            target_kind="discussion",
            target_number=number,
            discussion_id=discussion_id,
            message=message,
        )

    def add_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return self._skip("add_labels", target_kind="issue", target_number=number, labels=list(labels))

    def remove_labels(self, number: int, labels: List[str]) -> Dict[str, Any]:
        return self._skip("remove_labels", target_kind="issue", target_number=number, labels=list(labels))

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        return self._skip("create_issue", title=title, body=body, labels=list(labels or []))


class WorkflowOrchestrator:
    def __init__(self, project_root: Path, engine: Any, actions: Any, client: Any, config: Dict[str, Any]) -> None:
        self.project_root = Path(project_root)
        self.engine = engine
        self.actions = actions
        self.client = client
        self.config = config
        self.workflows_dir = self.project_root / "workflows"

    def process(self, event: Any) -> Dict[str, Any]:
        workflow = self._load_workflow(event.event_type)
        participants = sorted(
            workflow.get("participants", []),
            key=lambda participant: int(participant.get("priority", 0) or 0),
        )
        context: Dict[str, Any] = {"cache": {}}
        participant_results = []
        vetoed = False
        veto_reason = ""
        for participant in participants:
            result = self._execute_participant(event, participant, context=context)
            participant_results.append(
                {
                    "role": participant.get("role", "pm"),
                    "action_mode": participant.get("action_mode", "respond"),
                    "priority": participant.get("priority", 0),
                    "result": result,
                }
            )
            if participant.get("action_mode", "respond") == "veto" and result.get("vetoed"):
                self._escalate(event, "veto", result.get("veto_reason", ""))
                vetoed = True
                veto_reason = result.get("veto_reason", "")
                break

        failed_signals: List[Dict[str, str]] = []
        signals = workflow.get("signals", [])
        if signals:
            failed_signals = self._check_signals(event, signals)
            for failure in failed_signals:
                self._escalate(event, failure["type"], self._build_escalation_detail(event, failure))

        combined: Dict[str, Any] = {}
        if participant_results:
            last_result = participant_results[-1]["result"]
            if isinstance(last_result, dict):
                combined.update(last_result)
        combined["workflow"] = {
            "event_type": workflow.get("event_type", "default"),
            "participants": participants,
            "signals": signals,
            "vetoed": vetoed,
        }
        combined["participants"] = participant_results
        combined["signal_failures"] = failed_signals
        combined["escalated"] = vetoed or bool(failed_signals)
        combined["vetoed"] = vetoed
        if veto_reason:
            combined["veto_reason"] = veto_reason
        return combined

    def _load_workflow(self, event_type: str) -> Dict[str, Any]:
        candidates = [self.workflows_dir / f"{event_type}.yaml", self.workflows_dir / "default.yaml"]
        for path in candidates:
            if not path.exists():
                continue
            payload = yaml.safe_load(path.read_text()) or {}
            payload.setdefault("event_type", "default" if path.name == "default.yaml" else event_type)
            payload.setdefault("participants", [])
            payload.setdefault("signals", [])
            return payload
        raise FileNotFoundError(f"missing workflow config for event_type={event_type}")

    def _execute_participant(
        self,
        event: Any,
        participant: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._condition_matches(event, participant.get("condition"), context=context):
            return {"skipped": True, "reason": "condition_not_met"}

        role = participant.get("role", "pm")
        action_mode = participant.get("action_mode", "respond")
        if action_mode == "veto":
            return self.engine.run_veto_handler(event, role=role)

        original_actions = self.engine.actions
        original_run_ai_handler = self.engine.run_ai_handler

        def run_ai_handler_with_role(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            kwargs.setdefault("role", role)
            return original_run_ai_handler(*args, **kwargs)

        self.engine.run_ai_handler = run_ai_handler_with_role
        if action_mode == "observe":
            self.engine.actions = _NoOpActions()

        try:
            result = self.engine.process(event)
        finally:
            self.engine.actions = original_actions
            self.engine.run_ai_handler = original_run_ai_handler

        return result

    def _condition_matches(
        self,
        event: Any,
        condition: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not condition:
            return True
        if not isinstance(condition, dict):
            return False

        supported_keys = {"files_match", "labels_contain"}
        unknown_keys = set(condition) - supported_keys
        if unknown_keys:
            return False

        if "files_match" in condition:
            if event.target_kind != "pull_request" or not event.target_number:
                return False
            patterns = self._parse_patterns(condition.get("files_match"))
            changed_files = self._get_changed_files(event, context=context)
            if not patterns or not self._files_match_patterns(changed_files, patterns):
                return False

        if "labels_contain" in condition:
            required_labels = set(self._normalize_labels(condition.get("labels_contain")))
            event_labels = set(self._normalize_labels(event.metadata.get("labels", [])))
            if not required_labels or not (event_labels & required_labels):
                return False

        return True

    def _get_changed_files(self, event: Any, context: Optional[Dict[str, Any]] = None) -> List[str]:
        if event.target_kind != "pull_request" or not event.target_number:
            return []

        cache = (context or {}).setdefault("cache", {})
        cache_key = ("pull_request_files", event.repo, event.target_number)
        if cache_key not in cache:
            response = self.client.api(f"repos/{event.repo}/pulls/{event.target_number}/files", method="GET")
            cache[cache_key] = [
                item.get("filename", "")
                for item in (response if isinstance(response, list) else [])
                if isinstance(item, dict) and item.get("filename")
            ]
        return list(cache.get(cache_key, []))

    def _files_match_patterns(self, changed_files: List[str], patterns: List[str]) -> bool:
        for filename in changed_files:
            for pattern in patterns:
                if fnmatch.fnmatch(filename, pattern):
                    return True
                if pattern.startswith("**/") and fnmatch.fnmatch(filename, pattern[3:]):
                    return True
        return False

    def _parse_patterns(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [pattern.strip() for pattern in value.split("|") if pattern.strip()]
        if isinstance(value, (list, tuple, set)):
            patterns: List[str] = []
            for item in value:
                patterns.extend(self._parse_patterns(item))
            return patterns
        return [str(value).strip()] if str(value).strip() else []

    def _normalize_labels(self, values: Any) -> List[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values]
        if not isinstance(values, (list, tuple, set)):
            return [str(values)]

        normalized: List[str] = []
        for value in values:
            if isinstance(value, dict):
                label = value.get("name")
                if label:
                    normalized.append(str(label))
            elif value is not None:
                normalized.append(str(value))
        return normalized

    def _check_signals(self, event: Any, signals: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        failures: List[Dict[str, str]] = []
        for signal in signals:
            signal_type = signal.get("type")
            requirement = signal.get("require")
            if signal_type == "ci_checks":
                sha = event.metadata.get("head_sha") or event.metadata.get("sha")
                if not sha:
                    failures.append({"type": "ci_checks", "reason": "missing commit SHA for CI check lookup"})
                    continue

                response = self.client.api(f"repos/{event.repo}/commits/{sha}/check-runs", method="GET")
                check_runs = response.get("check_runs", []) if isinstance(response, dict) else []
                if requirement == "all_pass":
                    if not check_runs:
                        failures.append({"type": "ci_checks", "reason": "no CI check runs found"})
                        continue
                    failing = [
                        run.get("name") or str(run.get("id") or "unknown")
                        for run in check_runs
                        if run.get("status") != "completed" or run.get("conclusion") != "success"
                    ]
                    if failing:
                        failures.append(
                            {
                                "type": "ci_checks",
                                "reason": f"CI checks not passing: {', '.join(failing)}",
                            }
                        )
                continue

            if signal_type == "pr_approvals":
                if not event.target_number:
                    failures.append({"type": "pr_approvals", "reason": "missing pull request number for review lookup"})
                    continue

                response = self.client.api(f"repos/{event.repo}/pulls/{event.target_number}/reviews", method="GET")
                reviews = response if isinstance(response, list) else []
                latest_reviews: Dict[str, str] = {}
                for review in reviews:
                    user = (review.get("user") or {}).get("login")
                    if user:
                        latest_reviews[user] = (review.get("state") or "").upper()

                approvals = [user for user, state in latest_reviews.items() if state == "APPROVED"]
                if requirement == "minimum_1" and len(approvals) < 1:
                    failures.append({"type": "pr_approvals", "reason": "pull request has fewer than 1 approval"})
                continue

            failures.append({"type": signal_type or "unknown", "reason": "unsupported workflow signal"})
        return failures

    def _escalate(self, event: Any, reason_class: str, detail: str) -> None:
        target_number = event.target_number or 0
        key = f"{event.repo}#{target_number}:{event.event_type}:{reason_class}"
        title = f"[Agent ESCALATE] {key}"
        open_issues = self.client.api(
            f"repos/{event.repo}/issues?labels=agent-escalate&state=open",
            method="GET",
        )
        if isinstance(open_issues, list):
            for issue in open_issues:
                if key in issue.get("title", ""):
                    return

        had_dry_run = hasattr(self.actions, "dry_run")
        original_dry_run = getattr(self.actions, "dry_run", None)
        if had_dry_run:
            self.actions.dry_run = False
        try:
            self.actions.create_issue(title=title, body=detail, labels=["agent-escalate"])
        finally:
            if had_dry_run:
                self.actions.dry_run = original_dry_run

    def _build_escalation_detail(self, event: Any, failure: Dict[str, str]) -> str:
        target_number = event.target_number or 0
        return (
            f"Workflow signal failed.\n\n"
            f"repo: {event.repo}\n"
            f"event_type: {event.event_type}\n"
            f"target: {event.target_kind} #{target_number}\n"
            f"url: {event.url}\n"
            f"reason: {failure['reason']}"
        )
