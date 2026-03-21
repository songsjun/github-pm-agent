# TODOS

## Recently Completed

- [x] Add stage-specific skills: `clarify`, `scope-guard`, `blocked-work`, `review-readiness`, `release-readiness`, `memory-distill`
- [x] Add stage-specific prompts: `intake_clarify`, `spec_review`, `blocker_investigation`, `review_readiness`, `release_readiness`, `retro_summary`
- [x] Add lifecycle routing so AI-handled events stop defaulting to one generic prompt
- [x] Add validation tests for prompt/skill inventory and routing integrity
- [x] Add typed escalation metadata to action plans
- [x] Add durable artifact storage and prompt injection for `brief`, `spec_review`, `release_readiness`, and `retro_summary`
- [x] Add typed recurring signals, periodic retro summaries, and policy-vs-trend memory files
- [x] Add follow-up scheduling, reconcile mode, daemon mode, multi-repo polling, and runtime analytics
- [x] Add webhook ingestion hooks for local reconciliation
- [x] Make human-decision escalation explicit in docs and schema
- [x] Update README and architecture docs to match the current runtime surface

## P0: Unsupported Or Thinly Supported Event Handling

### Already collected but not specifically handled

- [x] `issue_changed`
- [x] `pull_request_changed`
- [x] `issue_comment`
- [x] `pull_request_review_comment`
- [x] `commit`
- [x] `workflow_failed` deterministic triage
- [x] `discussion` and `discussion_comment` convergence routing
- [x] `issue_event_unassigned`
- [x] `issue_event_unlabeled`
- [x] `issue_event_milestoned`
- [x] `issue_event_demilestoned`
- [x] generic `issue_event_*` observation fallback

### Synthetic states still missing

- [x] release-readiness
- [x] review-churn
- [x] repeated CI instability
- [x] stale discussion requiring decision
- [x] docs drift before release

## P0: Unsupported GitHub Actions

- [x] unassign owner
- [x] remove requested reviewer
- [x] mark PR as draft
- [x] mark PR as ready for review
- [x] merge PR
- [x] edit issue or PR title/body
- [x] set milestone
- [x] update GitHub Project fields
- [x] rerun workflow
- [x] cancel workflow run
- [x] create release
- [x] create or update discussion
- [x] submit review decision as reviewer (`approve` / `request changes`)

## P1: Architecture Improvements

- [x] Add stage + risk classification before prompt selection

## P1: Polling And Event Coverage

- [x] Add push / force-push / branch-ref signals
- [x] Add check suites / check runs / commit statuses
- [x] Add deployment and deployment-status events
- [x] Add release and tag events
- [x] Add GitHub Projects events
- [x] Add milestone-definition changes if they become operationally relevant
- [x] Replace regex-only mention detection with a stronger signal path if needed

## P1: Memory And Learning

- [x] Convert free-text memory summaries into typed recurring signals
- [x] Emit periodic retro summaries from recurring patterns
- [x] Distinguish repo policy memory from execution trend memory
- [x] Feed durable artifacts into future prompts alongside memory distill

## P2: Operational Maturity

- [x] Better release-readiness gating
- [x] Docs-drift detection
- [x] Optional second-opinion review mode for high-risk PRs
- [x] Multi-repo orchestration
- [x] Background worker or scheduled daemon mode
- [x] Analytics for event mix, action mix, escalation rate, and handler hit rate
