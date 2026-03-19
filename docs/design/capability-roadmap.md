# Capability Roadmap

## Scope

This roadmap translates the current `github-pm-agent` MVP into the next capability layers.
It focuses on:

- stage-specific skills and prompts
- lifecycle-aware event handling
- bounded automation with explicit escalation rules
- durable artifacts that improve future decisions

The roadmap is intentionally aligned with the current runtime:

- polling-driven event ingestion
- local queue and state
- local prompt and skill files
- deterministic handlers plus AI-assisted planning

## Current Baseline

| Area | Current state |
|---|---|
| Skills | Core policy plus stage skills for clarify, scope, blocked work, review readiness, release readiness, and memory distill |
| AI prompts | System prompt plus stage prompts for intake, spec, blocker, review, release, mention, and retro |
| Event routing | Deterministic handlers plus a lightweight lifecycle router for AI-handled events |
| Automation | Comments, labels, create issue, assign, request review, state change |
| Memory | Batched local distillation plus session transcript replay |
| Escalation | Implicit in handler behavior, not modeled as first-class policy |

## Capability Stages

| Stage | Purpose | Typical GitHub signals | Primary outputs |
|---|---|---|---|
| Intake | Clarify vague or open-ended work | `issue_changed`, `discussion`, `discussion_comment`, `mention` | brief, questions, narrowed next step |
| Scope Review | Keep work minimal and coherent | `issue_changed`, `pull_request_changed`, reopened work | scope note, spec review, risk note |
| Execution Support | Unblock stalled implementation | `workflow_failed`, `blocked` label, `blocked_issue_stale`, `issue_event_reopened` | blocker triage, owner follow-up, next-step plan |
| Review Readiness | Prepare work for efficient review | `stale_pr_review`, `pull_request_review_comment`, `pull_request_changed` | review readiness note, reviewer reminder |
| Release Readiness | Confirm merge/release readiness | merge candidate PRs, CI recovery, release prep | release summary, docs drift warning, readiness status |
| Learning | Distill recurring patterns | repeated review churn, CI failures, blocker aging | retro note, memory summary, policy update suggestion |

## Capability Assets

| Asset | Type | Stage | Status | Triggering events/phases | Main use |
|---|---|---|---|---|---|
| `skills/clarify.md` | skill | Intake | implemented | vague issues, new discussions, ambiguous mentions | ask forcing questions and produce a short brief |
| `skills/scope-guard.md` | skill | Scope Review | implemented | changing issues/PRs, reopened work | detect scope creep, enforce smallest next step |
| `skills/blocked-work.md` | skill | Execution Support | implemented | blocked items, workflow failures, reopened issues | require root cause, owner, next action, update time |
| `skills/review-readiness.md` | skill | Review Readiness | implemented | stale PRs, review churn, review comments | convert noisy review state into concrete next actions |
| `skills/release-readiness.md` | skill | Release Readiness | implemented | merge candidates, release prep | check CI, docs, changelog, rollout readiness |
| `skills/memory-distill.md` | skill | Learning | implemented | batch memory synthesis cycles | convert raw notes into durable patterns |
| `prompts/actions/intake_clarify.md` | prompt | Intake | implemented | issue/discussion intake | produce a brief or clarifying comment |
| `prompts/actions/spec_review.md` | prompt | Scope Review | implemented | changed issue/PR scope | produce bounded scope/risk guidance |
| `prompts/actions/blocker_investigation.md` | prompt | Execution Support | implemented | failed workflow, blocked or reopened item | produce blocker triage and follow-up |
| `prompts/actions/review_readiness.md` | prompt | Review Readiness | implemented | review friction, stale PRs | produce bounded review guidance |
| `prompts/actions/release_readiness.md` | prompt | Release Readiness | implemented | release or merge preparation | summarize readiness and missing checks |
| `prompts/actions/retro_summary.md` | prompt | Learning | implemented | periodic memory distill | summarize recurring patterns and policy gaps |

## Event-Phase Mapping

| Event or phase | Preferred stage | Default prompt/skill | Notes |
|---|---|---|---|
| `mention` | Intake | `mention_response.md` + `pm-core.md` | keep current bounded behavior |
| `issue_changed` | Intake or Scope Review | `intake_clarify.md` or `spec_review.md` | choose based on issue maturity |
| `discussion` / `discussion_comment` | Intake | `intake_clarify.md` + `clarify.md` | prefer summarization and narrowing |
| `pull_request_changed` | Scope Review or Review Readiness | `spec_review.md` or `review_readiness.md` | depends on PR age and review state |
| `workflow_failed` | Execution Support | `blocker_investigation.md` + `blocked-work.md` | should avoid generic fallback |
| `issue_event_reopened` | Execution Support | deterministic follow-up plus `blocked-work.md` | keep comments short |
| `blocked_issue_stale` | Execution Support | deterministic reminder | keep deterministic |
| `stale_pr_review` | Review Readiness | deterministic reminder plus `review-readiness.md` | keep deterministic |
| merge/release candidate | Release Readiness | `release_readiness.md` + `release-readiness.md` | future state, not yet wired |
| memory batch synthesis | Learning | `retro_summary.md` + `memory-distill.md` | batched, never per-event |

## Automation Boundaries

### Auto-executable

| Action | Allowed when |
|---|---|
| Comment | The action is procedural, status-seeking, or summarization-only |
| Add/remove labels | The rule is deterministic and the label semantics are already known |
| Create follow-up issue | The need is obvious and bounded |
| Assign owner | The owner is already named in the event or rule |
| Request reviewers | The reviewer target is explicit and procedural |
| Open/close state change | The rule is deterministic and non-product-defining |

### Must escalate to a human

| Decision | Why |
|---|---|
| Product scope change | This changes intent, not just execution |
| Architecture choice | Cross-cutting and hard to reverse |
| Breaking data or schema decisions | High-impact, irreversible once deployed |
| Merge or release approval | Requires accountability and broader context |
| Force-closing contentious work | Social and product risk |
| Automatically creating large numbers of follow-up issues | Can create spam and workflow debt |
| Any action with unclear owner or ambiguous evidence | The agent should summarize and escalate instead |

## Suggested Implementation Order

| Priority | Slice | Outcome |
|---|---|---|
| P0 | Add roadmap assets and TODO backlog | completed |
| P0 | Add stage-specific skills and prompt files | completed |
| P0 | Introduce lightweight lifecycle routing | completed |
| P0 | Add prompt/skill validation tests | completed |
| P1 | Add typed escalation metadata to action plans | explicit human decision boundaries |
| P1 | Add durable artifacts: brief, spec-review, release-readiness, retro | reusable downstream context |
| P2 | Add richer policy engine and cooldown/escalation rules | move beyond raw event routing |
| P2 | Add release and docs-drift gates | operational maturity |

## Exit Criteria For The Next Phase

- generic fallback is no longer the default for high-value event classes
- at least five stage-specific skills/prompts exist and are routable
- TODO backlog reflects only real remaining unsupported events and actions
- escalation boundaries are explicit in docs and schema
- validation tests fail if routed prompt/skill assets disappear
