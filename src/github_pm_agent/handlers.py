from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Tuple

from github_pm_agent.capability_routing import route_for_event
from github_pm_agent.models import Event

if TYPE_CHECKING:
    from github_pm_agent.engine import EventEngine


HandlerFn = Callable[["EventEngine", Event], Dict]


def resolve_handler(engine: "EventEngine", event: Event) -> Tuple[str, HandlerFn]:
    if event.event_type == "mention":
        return "mention", handle_mention
    if event.event_type == "workflow_run":
        return "workflow_run_observation", handle_workflow_run
    if event.event_type == "workflow_failed":
        return "workflow_failed", handle_workflow_failed
    if event.event_type == "issue_event_closed":
        return "issue_closed_observation", handle_issue_event_closed
    if event.event_type == "issue_event_reopened":
        return "issue_reopened_followup", handle_issue_event_reopened
    if event.event_type == "issue_event_assigned":
        return "issue_assigned_observation", handle_issue_event_assigned
    if event.event_type == "pull_request_review":
        state = (event.metadata.get("state") or "").upper()
        if state == "CHANGES_REQUESTED":
            return "pull_request_review_changes_requested", handle_pull_request_review_changes_requested
        if state == "APPROVED":
            return "pull_request_review_approved", handle_pull_request_review_approved
        return "pull_request_review", handle_fallback
    if event.event_type == "issue_event_review_requested":
        return "issue_event_review_requested", handle_issue_event_review_requested
    if event.event_type == "stale_pr_review":
        return "stale_pr_review", handle_stale_pr_review
    if event.event_type == "blocked_issue_stale":
        return "blocked_issue_stale", handle_blocked_issue_stale
    if event.event_type == "issue_event_labeled" and (event.metadata.get("label") or "") == "blocked":
        return "issue_blocked_label", handle_issue_blocked_label
    if event.event_type in {"discussion", "discussion_comment"}:
        return "discussion_ai", handle_discussion
    return "fallback_generic", handle_fallback


def handle_mention(engine: "EventEngine", event: Event) -> Dict:
    return engine.run_ai_handler(
        event,
        prompt_path="prompts/actions/mention_response.md",
    )


def handle_fallback(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_workflow_run(engine: "EventEngine", event: Event) -> Dict:
    conclusion = event.metadata.get("conclusion") or "unknown"
    status = event.metadata.get("status") or "unknown"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="non-failing workflow runs are informational and should not trigger PM follow-up",
            target_kind="workflow_run",
            target_number=event.target_number,
            memory_note=f"workflow run #{event.target_number or 0} observed with status={status} conclusion={conclusion}",
        ),
    )


def handle_workflow_failed(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_discussion(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_issue_event_closed(engine: "EventEngine", event: Event) -> Dict:
    closer = event.actor or "unknown actor"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="closed work items do not need immediate PM follow-up",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"work item #{event.target_number} was closed by @{closer}",
        ),
    )


def handle_issue_event_reopened(engine: "EventEngine", event: Event) -> Dict:
    actor = event.actor or "someone"
    message = (
        f"This work item was reopened by @{actor}.\n\n"
        "Please add a short status update with:\n"
        "1. why it was reopened\n"
        "2. the next concrete action\n"
        "3. who is driving it now\n"
        "4. when the next update should be expected"
    )
    plan = engine.make_plan(
        should_act=True,
        reason="reopened work items need a fresh status update to stay actionable",
        action_type="comment",
        target_kind="issue",
        target_number=event.target_number,
        message=message,
        memory_note=f"work item #{event.target_number} was reopened by @{actor}",
    )
    return engine.finish_plan(event, plan)


def handle_issue_event_assigned(engine: "EventEngine", event: Event) -> Dict:
    assignee = event.metadata.get("assignee") or "unknown assignee"
    actor = event.actor or "unknown actor"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="assignment changes are useful to remember but do not need an extra PM comment",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"work item #{event.target_number} was assigned to @{assignee} by @{actor}",
        ),
    )


def handle_pull_request_review_changes_requested(engine: "EventEngine", event: Event) -> Dict:
    reviewer = event.actor or "a reviewer"
    summary = _summarize_body(event.body)
    message = (
        f"Changes were requested by @{reviewer}.\n\n"
        f"Key note: {summary}\n\n"
        "Please address the requested changes and re-request review when ready."
    )
    plan = engine.make_plan(
        should_act=True,
        reason="pull request received changes requested review",
        action_type="comment",
        target_kind="pull_request",
        target_number=event.target_number,
        message=message,
        memory_note=f"changes requested on PR #{event.target_number} by @{reviewer}",
    )
    return engine.finish_plan(event, plan)


def handle_pull_request_review_approved(engine: "EventEngine", event: Event) -> Dict:
    reviewer = event.actor or "a reviewer"
    plan = engine.make_plan(
        should_act=False,
        reason="pull request was approved; no noisy follow-up needed",
        action_type="none",
        target_kind="pull_request",
        target_number=event.target_number,
        message="",
        memory_note=f"PR #{event.target_number} approved by @{reviewer}",
    )
    return engine.finish_plan(event, plan)


def handle_issue_event_review_requested(engine: "EventEngine", event: Event) -> Dict:
    reviewer = event.metadata.get("review_requested_reviewer") or ""
    requester = event.actor or "a contributor"
    if reviewer:
        message = (
            f"Review was requested from @{reviewer}.\n\n"
            f"@{requester} please keep the PR up to date and respond quickly to feedback."
        )
        plan = engine.make_plan(
            should_act=True,
            reason="review was requested on a pull request",
            action_type="comment",
            target_kind="pull_request",
            target_number=event.target_number,
            message=message,
            memory_note=f"review requested on PR #{event.target_number} for @{reviewer}",
        )
        return engine.finish_plan(event, plan)

    plan = engine.make_plan(
        should_act=False,
        reason="review requested event had no reviewer target",
        action_type="none",
        target_kind="pull_request",
        target_number=event.target_number,
        message="",
        memory_note=f"review requested on PR #{event.target_number} with missing reviewer",
    )
    return engine.finish_plan(event, plan)


def handle_stale_pr_review(engine: "EventEngine", event: Event) -> Dict:
    reviewers = [reviewer for reviewer in event.metadata.get("requested_reviewers", []) if reviewer]
    reviewers_text = ", ".join(f"@{reviewer}" for reviewer in reviewers) if reviewers else "no specific reviewers"
    author = event.metadata.get("author") or "author"
    hours_waiting = event.metadata.get("hours_waiting") or "many"
    message = (
        f"@{author} this PR has been waiting about {hours_waiting} hours without review.\n\n"
        f"Requested reviewers: {reviewers_text}.\n"
        "If the PR is ready, please re-request review or ping the right reviewer. "
        "If it is blocked, leave a short status update so the queue stays clear."
    )
    plan = engine.make_plan(
        should_act=True,
        reason="open PR appears stale and has no review yet",
        action_type="comment",
        target_kind="pull_request",
        target_number=event.target_number,
        message=message,
        memory_note=f"stale review reminder posted for PR #{event.target_number}",
    )
    return engine.finish_plan(event, plan)


def handle_blocked_issue_stale(engine: "EventEngine", event: Event) -> Dict:
    hours_blocked = event.metadata.get("hours_blocked") or "many"
    message = (
        f"This issue has been marked `blocked` for about {hours_blocked} hours without a new update.\n\n"
        "Please post:\n"
        "1. what is blocking it\n"
        "2. who owns the unblock step\n"
        "3. the next concrete action\n"
        "4. when the next update should be expected\n\n"
        "Remove the `blocked` label once work can continue."
    )
    plan = engine.make_plan(
        should_act=True,
        reason="blocked issue has gone stale",
        action_type="comment",
        target_kind="issue",
        target_number=event.target_number,
        message=message,
        memory_note=f"blocked issue reminder posted for issue #{event.target_number}",
    )
    return engine.finish_plan(event, plan)


def handle_issue_blocked_label(engine: "EventEngine", event: Event) -> Dict:
    message = (
        "This issue is now marked `blocked`.\n\n"
        "To keep the queue actionable, please add a short blocker note:\n"
        "1. blocker\n"
        "2. owner\n"
        "3. next unblock step\n"
        "4. expected next update time"
    )
    plan = engine.make_plan(
        should_act=True,
        reason="issue was newly marked blocked",
        action_type="comment",
        target_kind="issue",
        target_number=event.target_number,
        message=message,
        memory_note=f"blocked template requested on issue #{event.target_number}",
    )
    return engine.finish_plan(event, plan)


def _summarize_body(body: str) -> str:
    cleaned = " ".join((body or "").split())
    if not cleaned:
        return "No review body was provided."
    if len(cleaned) <= 180:
        return cleaned
    return f"{cleaned[:177].rstrip()}..."


def _memory_only_plan(
    engine: "EventEngine",
    *,
    reason: str,
    target_kind: str,
    target_number: int | None,
    memory_note: str,
) -> Dict:
    return engine.make_plan(
        should_act=False,
        reason=reason,
        action_type="none",
        target_kind=target_kind,
        target_number=target_number,
        message="",
        memory_note=memory_note,
    )


def _run_capability_route(engine: "EventEngine", event: Event) -> Dict:
    project_root = Path(engine.config.get("_project_root", ".")).resolve()
    route = route_for_event(project_root, event)
    result = engine.run_ai_handler(
        event,
        prompt_path=route.prompt_path,
        skill_refs=route.skill_refs,
    )
    result["routing"] = route.to_dict()
    return result
