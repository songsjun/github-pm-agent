# github-pm-agent

`github-pm-agent` is a polling-driven GitHub PM agent runtime for a target repository.

This repository is intentionally scoped as an MVP:

- single process
- synchronous execution
- local file-backed queue and state
- optional multi-repo polling from one local runtime
- `gh api` for GitHub reads/writes
- pluggable AI adapters
- prompt/template/skill library
- optional supervisory pass for memory extraction

## Why this shape

The goal is not to let an LLM freestyle over raw GitHub state.

The runtime is split into six small layers:

1. `poller`: fetch new GitHub state changes since the last cursor
2. `queue`: persist normalized events locally as JSONL
3. `engine`: classify events and dispatch them to handlers
4. `ai adapter`: unify model calls behind one request/response interface
5. `github actions`: wrap repo mutations with dry-run support
6. `supervisor`: optionally review important interactions and write memory notes

Deterministic work stays deterministic. AI is used for summarization, drafting, and judgment where text synthesis actually helps.

## What it covers

- repo notifications for stronger mention signals
- repo events for push, force-push, branch create/delete, and release signals
- issue, issue comment, issue event polling
- milestone polling and project change polling
- pull request, review, review comment polling
- workflow run, deployment, release, check-run, and commit-status polling
- commit polling from the default branch
- discussion and discussion comment polling through GraphQL
- mention detection from notifications plus bodies/comments
- local queue and cursor state
- follow-up scheduling and replay from local memory notes
- typed memory signals for policy and execution trends
- durable artifacts for brief/spec/release/retro reuse
- handler registry for event types
- `poll`, `cycle`, `reconcile`, `daemon`, `webhook`, `analytics`, and queue inspection commands
- provider/model-selectable AI adapter
- prompt, template, memory, and skill loading
- GitHub mutation helpers with dry-run mode, including merge/review/discussion/release/project actions
- optional second-opinion review mode for high-risk PRs

## What it does not cover yet

- concurrency
- database-backed state
- advanced scheduling
- autonomous merge/release behavior
- a fully declarative policy engine for every PM rule

Those can come later if the loop proves useful.

## Quick start

### Requirements

- Python 3.9+
- GitHub CLI installed and authenticated
- local Codex CLI and/or Gemini CLI installed if you want to use CLI-backed providers

Check `gh`:

```bash
/opt/homebrew/bin/gh auth status
```

Check local AI CLIs:

```bash
/opt/homebrew/bin/codex --version
/opt/homebrew/bin/gemini --version
```

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configure

Copy the example config:

```bash
cp config/example.json config/local.json
```

Then set the target repository, mentions to watch, and AI provider settings.

You can also provide `github.repos` as a list if you want one runtime to poll multiple repositories.

The example config already includes two local providers:

- `codex_cli` via `scripts/run_ai_cli.py`
- `gemini_cli` via `scripts/run_ai_cli.py`

`codex_cli` is the default because it is currently the more stable local path on this machine. For Gemini, prefer `gemini-2.5-flash` over the preview default because the preview model may reject requests when capacity is tight.

### Run one cycle

```bash
github-pm-agent cycle --config config/local.json
```

This does:

1. poll GitHub
2. enqueue normalized events
3. drain the queue
4. route each event to a handler
5. write proposed or executed actions

Current concrete handlers:

- `mention`: AI drafts a bounded response
- `issue_changed`, `issue_comment`, `pull_request_changed`, `pull_request_review_comment`, `commit`: stage-routed AI handling
- `stale_pr_review`: deterministic reminder on open PRs with no review after threshold
- `blocked_issue_stale`: deterministic reminder on long-blocked issues
- `workflow_failed`, `commit_status_failed`, `check_run_failed`: deterministic triage with escalation metadata
- `release_readiness`, `review_churn`, `repeated_ci_instability`, `stale_discussion_decision`, `docs_drift_before_release`: synthetic PM signals
- `issue_event_labeled` with label `blocked`: deterministic blocker-template comment
- unknown `issue_event_*`: memory-only observation fallback
- other events fall back to stage-aware AI routing rather than one generic prompt

### Useful commands

```bash
github-pm-agent poll --config config/local.json
github-pm-agent queue list --config config/local.json
github-pm-agent queue peek --config config/local.json --limit 5
github-pm-agent cycle --config config/local.json
github-pm-agent reconcile --config config/local.json
github-pm-agent analytics --config config/local.json
github-pm-agent daemon --config config/local.json --interval 60
github-pm-agent webhook --config config/local.json --event-type issues --payload-file payload.json
```

## Runtime layout

Everything is local files under `runtime/`:

- `runtime/cursors.json`
- `runtime/queue_pending.jsonl`
- `runtime/queue_done.jsonl`
- `runtime/queue_dead.jsonl`
- `runtime/seen_ids.json`
- `runtime/outbox.jsonl`
- `runtime/memory_notes.jsonl`
- `runtime/followups.jsonl`
- `runtime/sessions/`
- `runtime/memory/distilled.md`
- `runtime/memory/policy.md`
- `runtime/memory/trends.md`
- `runtime/memory/retro.md`

This keeps the MVP inspectable and easy to reset.

## Provider adapter

The normalized CLI adapter script is `scripts/run_ai_cli.py`.

It standardizes:

- provider selection: `codex` or `gemini`
- prompt file input
- cwd pinning
- optional schema handoff for Codex
- normalized JSON output back to the runtime

## Design decisions

- GitHub access uses `gh api` instead of a custom auth stack.
- Queue/state is JSONL plus a few small JSON files, not SQLite.
- Event handlers are plain Python functions registered by event type.
- AI session continuity is local and provider-agnostic first.
- All GitHub actions support `dry_run`; default config keeps it enabled.

## Suggested next steps

- introduce repo-specific PM policies
- add a more declarative cooldown and escalation policy layer
- add richer artifact types for release checklists and roadmap sync
