# github-pm-agent

`github-pm-agent` is a polling-driven GitHub PM agent runtime for a target repository.

This repository is intentionally scoped as an MVP:

- single process
- synchronous execution
- local file-backed queue and state
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

- issue, issue comment, issue event polling
- pull request, review, review comment polling
- workflow run polling
- commit polling from the default branch
- discussion and discussion comment polling through GraphQL
- mention detection from bodies/comments
- local queue and cursor state
- handler registry for event types
- provider/model-selectable AI adapter
- prompt, template, memory, and skill loading
- GitHub mutation helpers with dry-run mode

## What it does not cover yet

- concurrency
- background workers
- database-backed state
- webhook ingestion
- advanced scheduling
- cross-repo orchestration
- rich policy engine for every PM rule

Those can come later if the loop proves useful.

## Quick start

### Requirements

- Python 3.9+
- GitHub CLI installed and authenticated

Check `gh`:

```bash
/opt/homebrew/bin/gh auth status
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

### Useful commands

```bash
github-pm-agent poll --config config/local.json
github-pm-agent queue list --config config/local.json
github-pm-agent queue peek --config config/local.json --limit 5
github-pm-agent cycle --config config/local.json
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
- `runtime/sessions/`

This keeps the MVP inspectable and easy to reset.

## Design decisions

- GitHub access uses `gh api` instead of a custom auth stack.
- Queue/state is JSONL plus a few small JSON files, not SQLite.
- Event handlers are plain Python functions registered by event type.
- AI session continuity is local and provider-agnostic first.
- All GitHub actions support `dry_run`; default config keeps it enabled.

## Suggested next steps

- move from polling to `webhook + reconcile`
- add richer action planning on top of the event handlers
- introduce repo-specific PM policies
- add a second memory synthesizer tuned for long-lived projects

