# TODOS

## Recently Completed

- [x] Add stage-specific skills: `clarify`, `scope-guard`, `blocked-work`, `review-readiness`, `release-readiness`, `memory-distill`
- [x] Add stage-specific prompts: `intake_clarify`, `spec_review`, `blocker_investigation`, `review_readiness`, `release_readiness`, `retro_summary`
- [x] Add lifecycle routing so AI-handled events stop defaulting to one generic prompt
- [x] Add validation tests for prompt/skill inventory and routing integrity

## P0: Unsupported Or Thinly Supported Event Handling

### Already collected but not specifically handled

- [ ] `issue_changed`
- [ ] `pull_request_changed`
- [ ] `issue_comment`
- [ ] `pull_request_review_comment`
- [ ] `commit`
- [ ] `workflow_failed` still lacks deterministic triage
- [ ] `discussion` and `discussion_comment` lack dedicated convergence logic
- [ ] `issue_event_unassigned`
- [ ] `issue_event_unlabeled`
- [ ] `issue_event_milestoned`
- [ ] `issue_event_demilestoned`
- [ ] other `issue_event_*` variants still fall through generic handling

### Synthetic states still missing

- [ ] release-readiness
- [ ] review-churn
- [ ] repeated CI instability
- [ ] stale discussion requiring decision
- [ ] docs drift before release

## P0: Unsupported GitHub Actions

- [ ] unassign owner
- [ ] remove requested reviewer
- [ ] mark PR as draft
- [ ] mark PR as ready for review
- [ ] merge PR
- [ ] edit issue or PR title/body
- [ ] set milestone
- [ ] update GitHub Project fields
- [ ] rerun workflow
- [ ] cancel workflow run
- [ ] create release
- [ ] create or update discussion
- [ ] submit review decision as reviewer (`approve` / `request changes`)

## P1: Architecture Improvements

- [ ] Add typed escalation metadata to action plans
- [ ] Add durable artifacts: `brief`, `spec-review`, `release-readiness`, `retro-summary`
- [ ] Add stage + risk classification before prompt selection
- [ ] Add cooldown and follow-up scheduling semantics
- [ ] Add explicit human-decision boundaries to docs and schema
- [ ] Update README to match the current handler and action surface

## P1: Polling And Event Coverage

- [ ] Add webhook + reconcile mode
- [ ] Add push / force-push / branch-ref signals
- [ ] Add check suites / check runs / commit statuses
- [ ] Add deployment and deployment-status events
- [ ] Add release and tag events
- [ ] Add GitHub Projects events
- [ ] Add milestone-definition changes if they become operationally relevant
- [ ] Replace regex-only mention detection with a stronger signal path if needed

## P1: Memory And Learning

- [ ] Convert free-text memory summaries into typed recurring signals
- [ ] Emit periodic retro summaries from recurring patterns
- [ ] Distinguish repo policy memory from execution trend memory
- [ ] Feed durable artifacts into future prompts alongside memory distill

## P2: Operational Maturity

- [ ] Multi-repo orchestration
- [ ] Background worker or scheduled daemon mode
- [ ] Better release-readiness gating
- [ ] Docs-drift detection
- [ ] Optional second-opinion review mode for high-risk PRs
- [ ] Analytics for event mix, action mix, escalation rate, and handler hit rate
