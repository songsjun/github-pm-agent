from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Tuple

from github_pm_agent.models import Event

if TYPE_CHECKING:
    from github_pm_agent.engine import EventEngine


HandlerFn = Callable[["EventEngine", Event], Dict]


def resolve_handler(engine: "EventEngine", event: Event) -> Tuple[str, HandlerFn]:
    if event.event_type == "mention":
        return "mention", handle_mention
    if event.event_type == "stale_pr_review":
        return "stale_pr_review", handle_stale_pr_review
    if event.event_type == "blocked_issue_stale":
        return "blocked_issue_stale", handle_blocked_issue_stale
    if event.event_type == "issue_event_labeled" and (event.metadata.get("label") or "") == "blocked":
        return "issue_blocked_label", handle_issue_blocked_label
    return "fallback_generic", handle_fallback


def handle_mention(engine: "EventEngine", event: Event) -> Dict:
    return engine.run_ai_handler(
        event,
        prompt_path="prompts/actions/mention_response.md",
    )


def handle_fallback(engine: "EventEngine", event: Event) -> Dict:
    return engine.run_ai_handler(
        event,
        prompt_path="prompts/actions/default_event.md",
    )


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
