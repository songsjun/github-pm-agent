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
    risk_level: str = "normal"
    requires_human: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "prompt_path": self.prompt_path,
            "skill_refs": list(self.skill_refs),
            "reason": self.reason,
            "risk_level": self.risk_level,
            "requires_human": self.requires_human,
        }


def route_for_event(project_root: Path, event: Event) -> CapabilityRoute:
    if event.event_type in {
        "release_readiness",
        "docs_drift_before_release",
        "release_published",
        "release_draft",
        "milestone_changed",
    }:
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
            reason="release-oriented signals should use release-readiness framing",
            risk_level="high",
            requires_human=event.event_type in {"docs_drift_before_release", "release_published"},
        )

    if event.event_type in {"review_churn", "stale_pr_review"}:
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
            reason="review churn should be framed as review readiness",
            risk_level="normal",
            requires_human=False,
        )

    if event.event_type in {"repeated_ci_instability", "commit_status_failed", "check_run_failed"}:
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
            reason="CI instability should be framed as a blocker investigation",
            risk_level="urgent" if event.event_type == "repeated_ci_instability" else "high",
            requires_human=event.event_type == "repeated_ci_instability",
        )

    if event.event_type in {"stale_discussion_decision"}:
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
            reason="stale discussions need a decision-oriented clarification pass",
            risk_level="high",
            requires_human=True,
        )

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
            risk_level="high",
            requires_human=True,
        )

    if event.event_type in {"discussion", "discussion_comment", "issue_changed", "issue_comment"}:
        risk_level = "normal"
        requires_human = False
        if event.event_type in {"discussion", "discussion_comment"} and _looks_decision_needed(event):
            risk_level = "high"
            requires_human = True
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
            risk_level=risk_level,
            requires_human=requires_human,
        )

    if event.event_type in {"pull_request_changed", "pull_request_review_comment", "pull_request_review", "review_churn"}:
        risk_level = "normal"
        requires_human = False
        if event.event_type == "pull_request_changed" and (event.metadata.get("draft") or event.metadata.get("state") == "open"):
            risk_level = "high"
        if event.event_type == "pull_request_review" and (event.metadata.get("state") or "").upper() == "CHANGES_REQUESTED":
            risk_level = "high"
            requires_human = True
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
            risk_level=risk_level,
            requires_human=requires_human,
        )

    if event.event_type in {"commit", "push", "force_push", "branch_ref_created", "branch_ref_deleted"}:
        stage = "release_readiness" if event.event_type == "commit" else "clarify"
        prompt_candidates = (
            "prompts/actions/release_readiness.md",
            "prompts/actions/default_event.md",
        ) if stage == "release_readiness" else (
            "prompts/actions/intake_clarify.md",
            "prompts/actions/default_event.md",
        )
        skills = (
            "skills/release-readiness.md",
            "skills/pm-core.md",
        ) if stage == "release_readiness" else (
            "skills/clarify.md",
            "skills/pm-core.md",
        )
        return _route(
            project_root,
            stage=stage,
            prompt_candidates=prompt_candidates,
            skill_candidates=skills,
            reason="default-branch commits and branch-ref signals are best treated as delivery or topology changes",
            risk_level="normal" if event.event_type == "commit" else "low",
            requires_human=False,
        )

    if event.event_type in {"deployment", "deployment_status", "deployment_failed"}:
        return _route(
            project_root,
            stage="blocked_work" if event.event_type == "deployment_failed" else "release_readiness",
            prompt_candidates=(
                "prompts/actions/blocker_investigation.md" if event.event_type == "deployment_failed" else "prompts/actions/release_readiness.md",
                "prompts/actions/default_event.md",
            ),
            skill_candidates=(
                "skills/blocked-work.md" if event.event_type == "deployment_failed" else "skills/release-readiness.md",
                "skills/pm-core.md",
            ),
            reason="deployment signals should be reviewed as release or blocker signals",
            risk_level="high" if event.event_type == "deployment_failed" else "normal",
            requires_human=event.event_type == "deployment_failed",
        )

    if event.event_type == "project_changed":
        return _route(
            project_root,
            stage="clarify",
            prompt_candidates=(
                "prompts/actions/intake_clarify.md",
                "prompts/actions/default_event.md",
            ),
            skill_candidates=(
                "skills/clarify.md",
                "skills/pm-core.md",
            ),
            reason="project changes are coordination signals that need quick clarification, not immediate execution",
            risk_level="low",
            requires_human=False,
        )

    return _route(
        project_root,
        stage="generic_triage",
        prompt_candidates=("prompts/actions/default_event.md",),
        skill_candidates=("skills/pm-core.md",),
        reason="no stage-specific route exists yet for this event",
        risk_level="low",
        requires_human=False,
    )


def _route(
    project_root: Path,
    *,
    stage: str,
    prompt_candidates: Iterable[str],
    skill_candidates: Iterable[str],
    reason: str,
    risk_level: str = "normal",
    requires_human: bool = False,
) -> CapabilityRoute:
    prompts = tuple(prompt_candidates)
    skills = tuple(skill_candidates)
    return CapabilityRoute(
        stage=stage,
        prompt_path=_first_existing(project_root, prompts),
        skill_refs=_existing_refs(project_root, skills),
        reason=reason,
        risk_level=risk_level,
        requires_human=requires_human,
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


def _looks_decision_needed(event: Event) -> bool:
    body = f"{event.title}\n{event.body}".lower()
    if "?" in body:
        return True
    keywords = ("decide", "decision", "choose", "clarify", "should we")
    return any(keyword in body for keyword in keywords)
