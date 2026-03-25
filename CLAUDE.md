# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Run tests
python -m unittest discover -s tests

# Run a single test file
python -m unittest tests/test_engine.py

# Run a single test case
python -m unittest tests.test_engine.TestEventEngine.test_process

# Verify compilation
python -m compileall src tests scripts

# Run one full poll + process cycle
github-pm-agent cycle --config config/local.json

# Other CLI commands
github-pm-agent poll --config config/local.json
github-pm-agent reconcile --config config/local.json
github-pm-agent analytics --config config/local.json
github-pm-agent daemon --config config/local.json --interval 60
github-pm-agent queue list --config config/local.json
github-pm-agent queue peek --config config/local.json --limit 5
github-pm-agent queue retry --all --config config/local.json
github-pm-agent webhook --config config/local.json --event-type issues --payload-file payload.json
```

Config files are JSON or YAML. Start from `config/example.json` (copy to `config/local.json`).

## Architecture

The runtime has six layers, each in its own module:

```
poller -> queue_store -> engine -> ai_adapter -> actions -> memory_loop
```

1. **`poller.py`** — calls `gh api` to fetch new GitHub state changes since the last cursor, normalizes them into `Event` objects, and writes to the queue.
2. **`queue_store.py`** — JSONL-backed pending/done/dead queues plus a `seen_ids.json` dedup set.
3. **`engine.py` (`EventEngine`)** — the core dispatch layer. Calls `handlers.resolve_handler()` to pick a handler, runs it, then records the result via `memory_loop`.
4. **`handlers.py`** — maps `event.event_type` to handler functions. Most handlers delegate to `_run_capability_route()`, which calls `capability_routing.route_for_event()` to select a prompt and skill set, then calls `engine.run_ai_handler()`.
5. **`ai_adapter.py`** — unified `generate(AiRequest) -> AiResponse` interface. Supports `cli_script` providers (Codex CLI, Gemini CLI via `scripts/run_ai_cli.py`), `openai_compatible` REST providers, and `devenv_caps` for Docker-based execution.
6. **`memory_loop.py`** — records plan results, memory notes, and supervisor notes to `runtime/memory_notes.jsonl` and distilled markdown files.

### Key data flow

- `Event` (defined in `models.py`) is the normalized unit passed through all layers.
- `AiRequest` / `AiResponse` are the AI adapter boundary types.
- `ActionResult` is what the engine returns after executing a plan.
- The engine parses AI output as JSON (`action_plan.json` schema) and executes the chosen `action_type` against `actions.py` (which wraps `gh api` calls with dry-run support).

### Capability routing (`capability_routing.py`)

`route_for_event()` maps event types to a `CapabilityRoute` — a `(stage, prompt_path, skill_refs, risk_level, requires_human)` bundle. The route picks the first existing prompt file from a ranked candidate list. Stages: `clarify`, `review_readiness`, `release_readiness`, `blocked_work`, `generic_triage`.

### Multi-agent / coding flow

`workflow_orchestrator.py` handles multi-agent triggers. When the PM engine decides to delegate coding work, it spins up a `CodingSession` (`coding_session.py`) that:
1. Asks the AI to produce a `CodingPlan` (files + test command)
2. Executes the plan inside a Docker container via `DevEnvClient` (`devenv_client.py`)
3. Iterates up to `MAX_ITERATIONS=3` times on test failures
4. Creates a PR branch with the result

### Prompts and skills

- `prompts/system/pm.md` and `prompts/system/worker.md` are the system prompts for the PM and worker roles.
- `prompts/actions/` — action-specific prompts (one per stage/handler).
- `prompts/coding/` — prompts for the coding agent flow.
- `skills/` — optional skill overlays loaded alongside prompts.
- `templates/output/action_plan.json` and `action_plan.schema.json` define the structured output contract the AI must return.

### Runtime state (all local files)

Everything lives under `runtime/` (configurable via `runtime.state_dir`):
- `cursors.json` — per-source polling cursors
- `queue_pending.jsonl`, `queue_done.jsonl`, `queue_dead.jsonl`
- `seen_ids.json` — dedup set
- `outbox.jsonl` — proposed/executed actions
- `memory_notes.jsonl` — raw memory signals
- `sessions/` — per-target AI session turn logs
- `memory/distilled.md`, `policy.md`, `trends.md`, `retro.md`

### Config structure

Config is JSON or YAML with these top-level keys:
- `github.repo` / `github.repos` — target repo(s)
- `github.gh_path` — path to `gh` binary
- `github.mentions` — list of handles to watch
- `runtime.state_dir` — defaults to `"runtime"`
- `engine.dry_run` — when `true` (default), no GitHub mutations are executed
- `engine.supervisor_enabled` — enables a supervisory pass after each AI response
- `engine.second_opinion.enabled` — runs a second AI model on high-risk PR decisions
- `ai.default_provider`, `ai.providers` — provider registry

### Adding a new event handler

1. Add a branch in `handlers.resolve_handler()` for the new `event_type`.
2. Implement a `handle_*` function that calls `engine.finish_plan()` with a deterministic plan, or `_run_capability_route()` for AI routing.
3. If a new stage is needed, add a branch in `capability_routing.route_for_event()`.

### Tests

All tests are offline (no real GitHub or AI calls). Tests use `unittest` and mock `gh api` calls and AI providers. CI runs against Python 3.9 and 3.12.
