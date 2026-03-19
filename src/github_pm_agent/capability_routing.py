from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from github_pm_agent.models import Event


@dataclass(frozen=True)
class CapabilityRoute:
    stage: str
    prompt_path: str
    skill_refs: Tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "prompt_path": self.prompt_path,
            "skill_refs": list(self.skill_refs),
            "reason": self.reason,
        }


def route_for_event(project_root: Path, event: Event) -> CapabilityRoute:
    if event.event_type == "workflow_failed":
        return _route(
            project_root,
            stage="blocked_work",
            prompt_candidates=(
                "prompts/actions/blocker_investigation.md",
                "prompts/actions/default_event.md",
            ),
            skill_candidates=(
                "skills/blocked-work.md",
                "skills/pm-core.md",
            ),
            reason="workflow failures need blocker-oriented investigation framing",
        )

    if event.event_type in {"discussion", "discussion_comment", "issue_changed", "issue_comment"}:
        return _route(
            project_root,
            stage="clarify",
            prompt_candidates=(
                "prompts/actions/intake_clarify.md",
                "prompts/actions/default_event.md",
            ),
            skill_candidates=(
                "skills/clarify.md",
                "skills/scope-guard.md",
                "skills/pm-core.md",
            ),
            reason="discussion and issue churn often need clarification before execution",
        )

    if event.event_type in {"pull_request_changed", "pull_request_review_comment", "pull_request_review"}:
        return _route(
            project_root,
            stage="review_readiness",
            prompt_candidates=(
                "prompts/actions/review_readiness.md",
                "prompts/actions/default_event.md",
            ),
            skill_candidates=(
                "skills/review-readiness.md",
                "skills/scope-guard.md",
                "skills/pm-core.md",
            ),
            reason="pull-request events are best framed around review readiness and next steps",
        )

    if event.event_type == "commit":
        return _route(
            project_root,
            stage="release_readiness",
            prompt_candidates=(
                "prompts/actions/release_readiness.md",
                "prompts/actions/default_event.md",
            ),
            skill_candidates=(
                "skills/release-readiness.md",
                "skills/pm-core.md",
            ),
            reason="default-branch commits are best treated as delivery and release-readiness signals",
        )

    return _route(
        project_root,
        stage="generic_triage",
        prompt_candidates=("prompts/actions/default_event.md",),
        skill_candidates=("skills/pm-core.md",),
        reason="no stage-specific route exists yet for this event",
    )


def _route(
    project_root: Path,
    *,
    stage: str,
    prompt_candidates: Iterable[str],
    skill_candidates: Iterable[str],
    reason: str,
) -> CapabilityRoute:
    prompts = tuple(prompt_candidates)
    skills = tuple(skill_candidates)
    return CapabilityRoute(
        stage=stage,
        prompt_path=_first_existing(project_root, prompts),
        skill_refs=_existing_refs(project_root, skills),
        reason=reason,
    )


def _first_existing(project_root: Path, candidates: Tuple[str, ...]) -> str:
    for candidate in candidates:
        if (project_root / candidate).exists():
            return candidate
    return candidates[-1]


def _existing_refs(project_root: Path, candidates: Tuple[str, ...]) -> Tuple[str, ...]:
    existing: list[str] = []
    for candidate in candidates:
        if (project_root / candidate).exists() and candidate not in existing:
            existing.append(candidate)
    if existing:
        return tuple(existing)
    return (candidates[-1],)
