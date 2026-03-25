from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from github_pm_agent.utils import append_jsonl, read_jsonl, utc_now_iso
from github_pm_agent.workflow_instance import WorkflowInstance

# Keywords that indicate a pure confirmation (case-insensitive, matched anywhere in reply)
_CONFIRM_WORDS = re.compile(
    r"\b(ok|okay|确认|好的|lgtm|approve|proceed|go ahead|继续|通过|同意|yes|yep|yeah|done)\b",
    re.IGNORECASE,
)
# Keywords that indicate rejection / restart
_REJECT_WORDS = re.compile(
    r"\b(no|nope|不对|重来|重新|reject|redo|start over|不行|否|wrong)\b",
    re.IGNORECASE,
)
# Phrases that signal additional content alongside confirmation
_REVISE_SIGNALS = re.compile(
    r"(另外|补充|还有|but also|also add|add|additionally|plus|however|but|不过|但是|需要加上|加上|修改|改一下|change|update)",
    re.IGNORECASE,
)
# "Skip to <phase>" pattern — allows human to jump directly to a named phase
_SKIP_TO_RE = re.compile(
    r"(?:skip\s+to|跳到|直接到|jump\s+to|go\s+to)\s+(\S+)",
    re.IGNORECASE,
)


def classify_gate_response(text: str) -> str:
    """Classify an owner reply to a gate prompt.

    Returns one of: 'confirm', 'confirm_revise', 'reject', 'skip_to:<phase>', 'unclear'
    """
    t = text.strip()
    if not t:
        return "unclear"

    # Check "skip to <phase>" first — highest priority
    skip_match = _SKIP_TO_RE.search(t)
    if skip_match:
        return f"skip_to:{skip_match.group(1).strip()}"

    has_confirm = bool(_CONFIRM_WORDS.search(t))
    has_reject = bool(_REJECT_WORDS.search(t))
    has_revise = bool(_REVISE_SIGNALS.search(t))

    if has_reject and not has_confirm:
        return "reject"
    if has_confirm and has_revise:
        return "confirm_revise"
    if has_confirm:
        return "confirm"
    return "unclear"


class PhaseGateScanner:
    """Watches workflow-gate issues; re-queues discussion events when gates are resolved."""

    def __init__(self, queue: Any, client: Any, owner_login: str) -> None:
        self.queue = queue
        self.client = client
        self.owner_login = owner_login
        self.advanced_path = queue.runtime_dir / "gate_advanced.jsonl"

    def scan_and_advance(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        already_advanced = self._already_advanced()
        results: List[Dict[str, Any]] = []

        for state_path in workflows_dir.glob("*/*/state.json"):
            # Check clarification before gate — they're mutually exclusive states
            instance_check = WorkflowInstance(state_path)
            if instance_check.is_awaiting_clarification():
                clarification = instance_check.get_clarification()
                if clarification:
                    parts_c = state_path.parts
                    repo_c = parts_c[-3].replace("__", "/", 1)
                    number_c = int(parts_c[-2])
                    owner_c, name_c = (repo_c.split("/", 1) + [""])[:2]
                    answer = self._check_clarification_resolved(
                        owner_c, name_c, number_c, clarification["posted_at"]
                    )
                    if answer is not None:
                        instance_check.add_pending_comment(f"Clarification answer:\n{answer}")
                        instance_check.clear_clarification()
                        original_event = instance_check.get_original_event()
                        if original_event:
                            new_meta = dict(original_event.get("metadata", {}))
                            new_meta["advance_to_phase"] = clarification["phase"]
                            new_meta["artifacts"] = instance_check.get_artifacts()
                            new_meta["gate_human_comment"] = answer
                            append_jsonl(self.queue.pending_path, {**original_event, "metadata": new_meta})
                            results.append({
                                "repo": repo_c,
                                "discussion_number": number_c,
                                "from_phase": clarification["phase"],
                                "to_phase": clarification["phase"],
                                "response_type": "clarification_resume",
                            })
                continue  # don't also process gate for this instance

        for state_path in workflows_dir.glob("*/*/state.json"):
            # path structure: workflows/{safe_repo}/{number}/state.json
            parts = state_path.parts
            number_str = parts[-2]
            safe_repo = parts[-3]
            repo = safe_repo.replace("__", "/", 1)

            try:
                number = int(number_str)
            except ValueError:
                continue

            instance = WorkflowInstance(state_path)
            if instance.is_terminated() or instance.is_completed():
                continue

            next_phase = instance.get_gate_next_phase()
            if not next_phase:
                continue

            # Discussion-based gate (preferred)
            discussion_node_id = instance.get_discussion_gate_node_id()
            gate_posted_at = instance.get_gate_posted_at()
            if discussion_node_id and gate_posted_at:
                gate_key = f"{repo}:discussion:{number}:{next_phase}"
                if gate_key in already_advanced:
                    continue
                owner, name = (repo.split("/", 1) + [""])[:2]
                human_comment = self._check_discussion_gate_resolved(owner, name, number, gate_posted_at)
                if human_comment is None:
                    continue
                response_type, target_phase = self._classify_and_route(
                    instance, next_phase, human_comment
                )
                # For reject/confirm_revise, target_phase is current_phase (re-run), not next_phase.
                # Using a response-specific key prevents the confirm key from being consumed,
                # which would permanently block a future genuine confirm for next_phase.
                if response_type in ("confirm_revise", "reject"):
                    gate_key = f"{repo}:discussion:{number}:{next_phase}:{response_type}"
                self._advance(instance, repo, number, target_phase, human_comment, gate_key, response_type)
                results.append({
                    "repo": repo,
                    "discussion_number": number,
                    "from_phase": instance.get_phase(),
                    "to_phase": target_phase,
                    "response_type": response_type,
                })
                continue

            # Legacy issue-based gate
            gate_issue_number = instance.get_gate_issue_number()
            if gate_issue_number is None or (repo, gate_issue_number) in already_advanced:
                continue
            human_comment = self._check_issue_gate_resolved(gate_issue_number, repo)
            if human_comment is None:
                continue
            response_type, target_phase = self._classify_and_route(instance, next_phase, human_comment)
            self._advance(instance, repo, number, target_phase, human_comment, (repo, gate_issue_number), response_type)
            results.append({
                "repo": repo,
                "discussion_number": number,
                "from_phase": instance.get_phase(),
                "to_phase": target_phase,
                "response_type": response_type,
            })

        return results

    def _classify_and_route(
        self,
        instance: WorkflowInstance,
        next_phase: str,
        human_comment: str,
    ) -> tuple:
        """Classify the gate response and return (response_type, target_phase).

        - confirm         → advance to next_phase as planned
        - confirm_revise  → accumulate supplement, re-run the *current* PM synthesis phase
        - reject          → re-run the current PM synthesis phase with rejection reason
        - skip_to:<phase> → jump directly to the named phase (skip intermediate phases)
        - unclear         → advance anyway (treat as confirm) to avoid stalling
        """
        response_type = classify_gate_response(human_comment)
        current_phase = instance.get_phase() or next_phase
        if response_type.startswith("skip_to:"):
            target = response_type.split(":", 1)[1]
            return "skip_to", target
        if response_type == "confirm_revise":
            instance.add_user_supplement(current_phase, human_comment)
            return response_type, current_phase  # re-run current PM phase
        if response_type == "reject":
            return response_type, current_phase  # re-run current PM phase
        # confirm or unclear → advance
        return response_type, next_phase

    def _advance(
        self,
        instance: WorkflowInstance,
        repo: str,
        number: int,
        next_phase: str,
        human_comment: str,
        gate_key: Any,
        response_type: str = "confirm",
    ) -> None:
        original_event = instance.get_original_event()
        if not original_event:
            return
        current_phase = instance.get_phase()
        new_metadata = dict(original_event.get("metadata", {}))
        new_metadata["advance_to_phase"] = next_phase
        new_metadata["artifacts"] = instance.get_artifacts()
        new_metadata["gate_human_comment"] = human_comment
        new_metadata["gate_response_type"] = response_type
        resumed_event_dict = {**original_event, "metadata": new_metadata}
        append_jsonl(self.queue.pending_path, resumed_event_dict)
        append_jsonl(
            self.advanced_path,
            {
                "gate_key": str(gate_key),
                "repo": repo,
                "discussion_number": number,
                "from_phase": current_phase,
                "to_phase": next_phase,
                "response_type": response_type,
                "advanced_at": utc_now_iso(),
            },
        )
        instance.clear_gate()

    def _already_advanced(self) -> Set[Any]:
        result = set()
        for item in read_jsonl(self.advanced_path):
            gate_key = item.get("gate_key")
            if gate_key:
                result.add(gate_key)
            # legacy format
            repo = item.get("repo")
            gate_issue = item.get("gate_issue_number")
            if repo and gate_issue is not None:
                result.add((repo, gate_issue))
        return result

    def _check_discussion_gate_resolved(
        self, owner: str, name: str, number: int, gate_posted_at: str
    ) -> Optional[str]:
        """Return owner's comment text if they replied after gate_posted_at, else None."""
        if not self.owner_login:
            return None
        try:
            comments = self.client.get_discussion_comments(owner, name, number)
        except Exception:
            return None
        for comment in comments:
            if comment.get("createdAt", "") <= gate_posted_at:
                continue
            login = (comment.get("author") or {}).get("login", "")
            if login == self.owner_login:
                return comment.get("body") or ""
        return None

    def _check_issue_gate_resolved(self, issue_number: int, repo: str) -> Optional[str]:
        """Return human comment text if gate issue is resolved, None if still open."""
        if self.owner_login:
            comments = self.client.api(f"repos/{repo}/issues/{issue_number}/comments", method="GET")
            if isinstance(comments, list):
                for comment in comments:
                    login = (comment.get("user") or {}).get("login", "")
                    if login == self.owner_login:
                        return comment.get("body") or ""
        issue = self.client.api(f"repos/{repo}/issues/{issue_number}", method="GET")
        if isinstance(issue, dict) and issue.get("state") == "closed":
            return ""
        return None

    def _check_clarification_resolved(
        self, owner: str, name: str, number: int, posted_at: str
    ) -> Optional[str]:
        """Return owner's reply to the clarification comment if posted after posted_at, else None."""
        if not self.owner_login:
            return None
        try:
            comments = self.client.get_discussion_comments(owner, name, number)
        except Exception:
            return None
        for comment in comments:
            if comment.get("createdAt", "") <= posted_at:
                continue
            login = (comment.get("author") or {}).get("login", "")
            if login == self.owner_login:
                return comment.get("body") or ""
        return None
