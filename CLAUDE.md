# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
pip install -e .                              # Install in editable mode
python -m unittest discover -s tests          # Run all tests
python -m unittest tests.test_engine          # Run a single test module
python -m unittest tests.test_engine.TestEngine.test_method  # Run a single test
python -m compileall src tests scripts        # Verify sources compile (CI check)
```

CLI entry point: `github-pm-agent <command>` or `python -m github_pm_agent.cli <command>`

Commands: `poll`, `cycle`, `reconcile`, `analytics`, `daemon`, `webhook`, `queue` (with subcommands: list, peek, dead, done, retry, replay).

## Architecture

This is a **polling-driven, single-process GitHub PM agent** that monitors repos, classifies events, and dispatches them to AI-augmented handlers. Design principle: deterministic work stays deterministic; AI is used only for summarization, drafting, and judgment.

### Event Flow

```
GitHub REST/GraphQL → Poller → Queue (JSONL) → Engine → Handlers → Actions → Queue (done/dead)
                                                  ↓
                                              AI Adapter → LLM
                                                  ↓
                                              Memory Loop → Artifacts
```

### Core Layers (in `src/github_pm_agent/`)

| Layer | Key Modules | Role |
|-------|-------------|------|
| **CLI / App** | `cli.py`, `app.py` | Entry point, orchestrates poll/cycle/reconcile/daemon |
| **Polling** | `poller.py`, `github_client.py` | Fetches GitHub state via `gh api` CLI (not a custom SDK) |
| **Queue** | `queue_store.py` | JSONL-backed persistence: pending, done, dead, suspended, resumed |
| **Engine** | `engine.py`, `handlers.py`, `capability_routing.py` | Classifies events, resolves handlers (20+ types), dispatches |
| **AI** | `ai_adapter.py`, `prompt_library.py`, `session_store.py` | Renders prompts with memory/skill injection, calls providers (shell, CLI script, OpenAI-compatible, DevEnv) |
| **Actions** | `actions.py` | GitHub mutations (comment, label, merge, etc.) with dry-run support |
| **Memory** | `memory_loop.py`, `artifact_store.py` | Persists notes, distilled/policy/trend/retro summaries, durable artifacts |
| **Multi-Agent** | `workflow_orchestrator.py`, `role_registry.py`, `coding_session.py` | Role-based orchestration (PM, worker, engineer, security) with permission controls |

### Data Model

Core types in `models.py`: `Event`, `AiRequest`, `AiResponse`, `ActionResult`.

### Runtime State

All state lives under `runtime/` (configurable): `cursors.json`, `queue_*.jsonl`, `seen_ids.json`, `outbox.jsonl`, `memory_notes.jsonl`, `followups.jsonl`, `sessions/`, `memory/`.

### Supporting Directories

- `prompts/` — System prompts per role + action prompts per stage (intake, spec review, release readiness, etc.)
- `skills/` — Skill documents injected into prompts (pm-core, clarify, blocked-work, etc.)
- `roles/` — Role definitions with `system.md` and `permissions.json` per role
- `templates/output/` — JSON schemas for AI output (action plans, supervisor notes)
- `config.example.yaml` — Reference config showing multi-agent, multi-repo setup

## Configuration

Config is JSON or YAML. Key sections: `github` (repos, tokens), `agents` (multi-agent with role-based tokens), `engine` (dry_run, continue_on_error), `ai` (provider config), `devenv` (container URL).

## Testing Notes

- Python 3.9+ with stdlib `unittest` (no pytest)
- CI matrix: Python 3.9 and 3.12
- Tests use extensive fixture mocking; no external services needed
- Only dependency: PyYAML
