# Repository Guidelines

## Project Structure & Module Organization
- `src/github_pm_agent/`: core runtime, CLI, poller, engine, and adapters.
- `tests/`: unit tests (standard library `unittest`).
- `config/` and `config.example.yaml`/`config.example.json`: configuration samples.
- `prompts/`, `templates/`, `skills/`, `roles/`, `workflows/`: prompt and workflow assets.
- `runtime/`: local state (queues, cursors, memory notes). Treat as ephemeral.
- `scripts/`: helper scripts, including `scripts/run_ai_cli.py`.

## Build, Test, and Development Commands
- `python3 -m venv .venv && source .venv/bin/activate`: create and activate a virtualenv.
- `pip install -e .`: install the package in editable mode.
- `github-pm-agent cycle --config config/local.json`: run one poll + queue drain cycle.
- `github-pm-agent poll --config config/local.json`: only poll and enqueue.
- `github-pm-agent daemon --config config/local.json --interval 60`: continuous polling.

## Coding Style & Naming Conventions
- Python 3.9+ with 4-space indentation and type hints where practical.
- Prefer `snake_case` for functions/variables and `PascalCase` for classes.
- Keep modules small and focused; use `pathlib.Path` for filesystem paths.

## Testing Guidelines
- Tests live in `tests/` and use `unittest`.
- Run the suite with `python -m unittest discover -s tests`.
- Name tests as `tests/test_*.py` and methods as `test_*`.

## Commit & Pull Request Guidelines
- Commit history favors conventional prefixes: `feat:`, `fix:`, `docs:`.
- Use concise, imperative subjects (e.g., `fix: handle empty queue`).
- PRs should include a clear summary and test evidence when relevant.
- Note any config or runtime impacts (e.g., new keys in `config/*.json`).

## Configuration & Runtime Notes
- Copy `config/example.json` to `config/local.json` and update repo, mentions, and provider settings.
- Keep secrets out of version control; `runtime/` is local-only state.
- `scripts/run_ai_cli.py` normalizes CLI-backed AI providers.
