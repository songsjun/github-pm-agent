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
    if event.event_type == "issue_changed":
        return "issue_changed_ai", handle_issue_changed
    if event.event_type == "issue_comment":
        return "issue_comment_ai", handle_issue_comment
    if event.event_type == "pull_request_changed":
        return "pull_request_changed_ai", handle_pull_request_changed
    if event.event_type == "pull_request_review_comment":
        return "pull_request_review_comment_ai", handle_pull_request_review_comment
    if event.event_type == "commit":
        return "commit_ai", handle_commit
    if event.event_type == "milestone_changed":
        return "milestone_changed_ai", handle_milestone_changed
    if event.event_type == "project_changed":
        return "project_changed_ai", handle_project_changed
    if event.event_type == "push":
        return "push_signal", handle_push
    if event.event_type == "force_push":
        return "force_push_signal", handle_force_push
    if event.event_type == "branch_ref_created":
        return "branch_ref_created_signal", handle_branch_ref_created
    if event.event_type == "branch_ref_deleted":
        return "branch_ref_deleted_signal", handle_branch_ref_deleted
    if event.event_type == "workflow_run":
        return "workflow_run_observation", handle_workflow_run
    if event.event_type == "workflow_failed":
        return "workflow_failed", handle_workflow_failed
    if event.event_type == "commit_status_failed":
        return "commit_status_failed", handle_commit_status_failed
    if event.event_type == "commit_status_pending":
        return "commit_status_pending", handle_commit_status_pending
    if event.event_type == "check_run_failed":
        return "check_run_failed", handle_check_run_failed
    if event.event_type == "check_run_pending":
        return "check_run_pending", handle_check_run_pending
    if event.event_type == "issue_event_closed":
        return "issue_closed_observation", handle_issue_event_closed
    if event.event_type == "issue_event_reopened":
        return "issue_reopened_followup", handle_issue_event_reopened
    if event.event_type == "issue_event_assigned":
        return "issue_assigned_observation", handle_issue_event_assigned
    if event.event_type == "issue_event_unassigned":
        return "issue_unassigned_observation", handle_issue_event_unassigned
    if event.event_type == "issue_event_unlabeled":
        return "issue_unlabeled_observation", handle_issue_event_unlabeled
    if event.event_type == "issue_event_milestoned":
        return "issue_milestoned_observation", handle_issue_event_milestoned
    if event.event_type == "issue_event_demilestoned":
        return "issue_demilestoned_observation", handle_issue_event_demilestoned
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
    if event.event_type == "repeated_ci_instability":
        return "repeated_ci_instability", handle_repeated_ci_instability
    if event.event_type == "release_readiness":
        return "release_readiness", handle_release_readiness
    if event.event_type == "project_release_ready":
        return "project_release_ready", handle_project_release_ready
    if event.event_type == "review_churn":
        return "review_churn", handle_review_churn
    if event.event_type == "stale_discussion_decision":
        return "stale_discussion_decision", handle_stale_discussion_decision
    if event.event_type == "docs_drift_before_release":
        return "docs_drift_before_release", handle_docs_drift_before_release
    if event.event_type == "release_published":
        return "release_published", handle_release_published
    if event.event_type == "release_draft":
        return "release_draft", handle_release_readiness
    if event.event_type in {"deployment", "deployment_status"}:
        return "deployment_signal", handle_deployment_signal
    if event.event_type == "deployment_failed":
        return "deployment_failed", handle_deployment_signal
    if event.event_type == "issue_event_labeled" and (event.metadata.get("label") or "") == "blocked":
        return "issue_blocked_label", handle_issue_blocked_label
    if event.event_type.startswith("issue_event_"):
        return "issue_event_generic_observation", handle_issue_event_generic
    if event.event_type in {"discussion", "discussion_comment"}:
        return "discussion_ai", handle_discussion
    return "fallback_generic", handle_fallback


def handle_mention(engine: "EventEngine", event: Event) -> Dict:
    return engine.run_raw_text_handler(
        event,
        prompt_path="prompts/actions/mention_response.md",
    )


def handle_issue_changed(engine: "EventEngine", event: Event) -> Dict:
    labels = {str(label).strip() for label in event.metadata.get("labels", []) if str(label).strip()}
    if "workflow-gate" in labels:
        return engine.finish_plan(
            event,
            _memory_only_plan(
                engine,
                reason="workflow-gate issues are synthetic control-plane state and do not need issue analysis",
                target_kind="issue",
                target_number=event.target_number,
                memory_note=f"workflow gate issue #{event.target_number or 0} updated",
            ),
        )
    return _run_capability_route(engine, event)


def handle_issue_comment(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_pull_request_changed(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_pull_request_review_comment(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_commit(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_milestone_changed(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_project_changed(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_push(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_force_push(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_branch_ref_created(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_branch_ref_deleted(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


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
    status = event.metadata.get("status") or "unknown"
    conclusion = event.metadata.get("conclusion") or "failure"
    evidence = [
        f"status={status}",
        f"conclusion={conclusion}",
    ]
    options = [
        "inspect the failing workflow logs",
        "re-run after fixing the blocker",
    ]
    if event.url:
        evidence.append(event.url)
    return engine.finish_plan(
        event,
        engine.make_plan(
            should_act=False,
            reason="workflow failure should be investigated before any follow-up action",
            action_type="none",
            target_kind="workflow_run",
            target_number=event.target_number,
            message="",
            memory_note=f"workflow failure observed for run #{event.target_number or 0}",
            needs_human_decision=True,
            human_decision_reason="workflow failure needs owner triage and a failing-job summary",
            urgency="high",
            evidence=evidence,
            options=options,
        ),
    )


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


def handle_issue_event_unassigned(engine: "EventEngine", event: Event) -> Dict:
    assignee = event.metadata.get("assignee") or "unknown assignee"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="unassignment should be recorded but does not need an extra response",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"work item #{event.target_number} was unassigned from @{assignee}",
        ),
    )


def handle_issue_event_unlabeled(engine: "EventEngine", event: Event) -> Dict:
    label = event.metadata.get("label") or "unknown label"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="label removal is useful context but usually not worth a new comment",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"label `{label}` was removed from issue #{event.target_number}",
        ),
    )


def handle_issue_event_milestoned(engine: "EventEngine", event: Event) -> Dict:
    milestone = event.metadata.get("milestone") or "unknown milestone"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="milestone assignment should be recorded for release tracking",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"issue #{event.target_number} was added to milestone {milestone}",
        ),
    )


def handle_issue_event_demilestoned(engine: "EventEngine", event: Event) -> Dict:
    milestone = event.metadata.get("milestone") or "unknown milestone"
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="milestone removal should be recorded for release tracking",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"issue #{event.target_number} was removed from milestone {milestone}",
        ),
    )


def handle_issue_event_generic(engine: "EventEngine", event: Event) -> Dict:
    event_name = event.metadata.get("event") or event.event_type.replace("issue_event_", "")
    return engine.finish_plan(
        event,
        _memory_only_plan(
            engine,
            reason="unclassified issue timeline events are stored as observations",
            target_kind="issue",
            target_number=event.target_number,
            memory_note=f"issue #{event.target_number} emitted issue event `{event_name}`",
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


def handle_commit_status_failed(engine: "EventEngine", event: Event) -> Dict:
    context = event.metadata.get("context") or "status check"
    state = event.metadata.get("state") or "failure"
    plan = engine.make_plan(
        should_act=False,
        reason="commit status failure should be investigated before action",
        action_type="none",
        target_kind="commit",
        target_number=None,
        message="",
        memory_note=f"commit status failed for {event.metadata.get('sha') or 'unknown sha'} ({context})",
        needs_human_decision=True,
        human_decision_reason="failed commit status needs owner triage",
        urgency="high",
        evidence=[f"state={state}", f"context={context}"],
        options=["inspect the failing job", "retry after fixing the blocker"],
    )
    return engine.finish_plan(event, plan)


def handle_check_run_failed(engine: "EventEngine", event: Event) -> Dict:
    name = event.metadata.get("name") or "check run"
    conclusion = event.metadata.get("conclusion") or "failure"
    plan = engine.make_plan(
        should_act=False,
        reason="check-run failure should be investigated before action",
        action_type="none",
        target_kind="commit",
        target_number=None,
        message="",
        memory_note=f"check run failed for {event.metadata.get('sha') or 'unknown sha'} ({name})",
        needs_human_decision=True,
        human_decision_reason="failed check run needs a root-cause summary",
        urgency="high",
        evidence=[f"conclusion={conclusion}", f"name={name}"],
        options=["inspect the failing check", "re-run after blocker removal"],
    )
    return engine.finish_plan(event, plan)


def handle_commit_status_pending(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_check_run_pending(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_release_readiness(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_project_release_ready(engine: "EventEngine", event: Event) -> Dict:
    metadata = event.metadata or {}
    tag_name = str(metadata.get("tag_name") or "").strip() or "v0.1.0"
    release_name = str(metadata.get("release_name") or "").strip() or f"Release {tag_name}"
    release_body = str(metadata.get("release_body") or event.body or "").strip()
    target_commitish = str(metadata.get("target_commitish") or "main").strip() or "main"
    merged_pr_count = int(metadata.get("merged_pr_count") or 0)
    plan = engine.make_plan(
        should_act=True,
        reason="all managed implementation workflows completed and repository has no remaining open work",
        action_type="create_release",
        target_kind="repo",
        target_number=None,
        message="",
        action_input={
            "tag_name": tag_name,
            "name": release_name,
            "body": release_body,
            "target_commitish": target_commitish,
            "draft": False,
            "prerelease": False,
            "generate_release_notes": False,
        },
        memory_note=f"project release {tag_name} prepared after {merged_pr_count} merged pull request(s)",
    )
    return engine.finish_plan(event, plan)


def handle_review_churn(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_repeated_ci_instability(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_stale_discussion_decision(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_docs_drift_before_release(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_release_published(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


def handle_deployment_signal(engine: "EventEngine", event: Event) -> Dict:
    return _run_capability_route(engine, event)


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
        risk_level=route.risk_level,
        requires_human=route.requires_human,
    )
    result["routing"] = route.to_dict()
    return result
