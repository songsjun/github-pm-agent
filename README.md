# github-pm-agent

`github-pm-agent` is a polling-driven GitHub project manager for multi-agent software delivery. It watches GitHub state, runs structured PM/worker workflows, opens and reviews PRs, and can now finish the loop by creating a release once delivery gates are satisfied.

## Overview

The runtime is built around deterministic control flow with bounded AI usage.

- GitHub events are polled and normalized into a local queue.
- Workflow YAML files define discussion, coding, and recovery phases.
- Agents collaborate through prompts, artifacts, and GitHub comments instead of hidden memory.
- High-risk state changes stay machine-checked: tests, mergeability, gate handling, and release creation are explicit steps.

## Core Workflow

The main project flow is:

1. `discussion` — clarify the request, produce requirements, review a technical design, and break work into GitHub issues.
2. `issue_coding` — implement on per-issue branches, run tests, open PRs, review, fix, and merge.
3. `project_release_ready` — when all managed coding issues are complete and the repo is clean, create a GitHub Release.

Release is intentionally gated by repository documentation. A release is blocked if the target repository README is missing required sections for overview, install, run, or deployment.

## Features

- Multi-agent GitHub workflow orchestration
- Discussion-driven planning before coding starts
- Deterministic gate handling with human confirmation support
- Isolated coding sessions with test execution in DevEnv
- Recovery scanners for stalled workflows, merge conflicts, and repo-state drift
- GitHub mutations for comments, labels, reviews, merges, discussions, and releases
- Local file-backed runtime for queue, memory, sessions, artifacts, and cursors
- CLI entrypoints for `poll`, `cycle`, `reconcile`, `daemon`, `webhook`, and queue inspection

## Repository Layout

- `src/github_pm_agent/` — runtime, queue, orchestrator, handlers, scanners, GitHub client
- `workflows/` — YAML workflow definitions
- `prompts/` — system, discussion, coding, and action prompts
- `roles/` — role configuration used by the orchestrator
- `scripts/` — local helpers, AI CLI wrapper, E2E runner, local DevEnv server
- `config/` — example configs and E2E configs
- `tests/` — unit tests for orchestrator, scanners, handlers, poller, coding session, adapters
- `.runtime/` or `runtime/` — local state, queues, logs, artifacts, sessions

## Install

Requirements:

- Python 3.9+
- GitHub CLI authenticated for the accounts you want to use
- `codex` and/or `gemini` CLI if you use CLI-backed providers

Install locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Check local tools:

```bash
gh auth status
codex --version
gemini --version
```

## Configure

Start from the example config:

```bash
cp config/example.json config/local.json
```

Key fields:

- `github.repo` or `github.repos` — target repository list
- `github.default_branch` — release and merge base branch
- `engine.dry_run` — keep `true` while validating behavior
- `ai.default_provider` — usually `codex_cli`
- `runtime.state_dir` — local queue and state directory

For the multi-account E2E flow, see `config/e2e-weather.yaml` and the generated live config `config/e2e-weather-live.json`.

## Run

Run one full poll/process cycle:

```bash
github-pm-agent --config config/local.json cycle
```

Useful commands:

```bash
github-pm-agent --config config/local.json poll
github-pm-agent --config config/local.json reconcile
github-pm-agent --config config/local.json daemon --interval 60
github-pm-agent --config config/local.json analytics
github-pm-agent --config config/local.json queue peek --limit 10
```

What `cycle` does:

1. poll GitHub
2. enqueue normalized events
3. drain the queue
4. run workflow scanners and gate scanners
5. apply actions or record failures

## E2E Demo

The repository includes a full end-to-end runner for the Weather Atlas demo:

```bash
python3 scripts/e2e_weather_runner.py start
python3 scripts/e2e_weather_runner.py status
python3 scripts/e2e_weather_runner.py confirm --comment "approve"
python3 scripts/e2e_weather_runner.py stop
```

This runner can:

- generate a requirements file
- create a GitHub repo and discussion
- start local background services
- monitor workflow state under `.runtime/e2e-weather/`

## Deployment

### Docker

Build the runtime image:

```bash
docker build -t github-pm-agent:latest .
```

Run it with a config path and GitHub credentials:

```bash
docker run --rm \
  -e CONFIG_PATH=/app/config/devenv.yaml \
  -e GITHUB_TOKEN_PM=... \
  -e GITHUB_TOKEN_ENGINEER=... \
  -e GITHUB_TOKEN_SECURITY=... \
  github-pm-agent:latest
```

### DevEnv

A DevEnv-oriented config is provided in `config/devenv.yaml`. It assumes the capability bridge will inject `DEVENV_CAPS_URL` and that GitHub tokens are passed as environment variables.

Typical shape:

```bash
devenv build . --tag pm-agent:latest
devenv run pm-agent:latest \
  --env CONFIG_PATH=/app/config/devenv.yaml \
  --env GITHUB_TOKEN_PM=... \
  --env GITHUB_TOKEN_ENGINEER=... \
  --env GITHUB_TOKEN_SECURITY=...
```

## Runtime State

The runtime is intentionally file-backed and inspectable.

Common files:

- `runtime/cursors.json`
- `runtime/queue_pending.jsonl`
- `runtime/queue_done.jsonl`
- `runtime/queue_dead.jsonl`
- `runtime/outbox.jsonl`
- `runtime/sessions/`
- `runtime/memory/`
- `.runtime/e2e-weather/logs/`

This makes failure analysis and replay straightforward.

## Release Gate

A managed project is only releaseable when all of the following are true:

- the `discussion` workflow completed
- all `issue_coding` workflows completed
- no business issues remain open
- no PRs remain open
- unreleased merged PRs exist
- the target repo README includes:
  - project overview
  - install instructions
  - run/usage instructions
  - deployment instructions

If the README is missing or incomplete, the runtime creates a `ready-to-code` issue titled `Write release README` instead of releasing immediately.

When those checks pass, the runtime emits `project_release_ready` and executes `create_release` against GitHub.

## Limitations

Current gaps are still real:

- queue/state is local JSONL, not database-backed
- concurrency is intentionally limited
- release notes are deterministic but still simple
- repo-specific policies are prompt- and config-driven, not a full policy DSL
- workflow quality still depends on prompt contracts and test quality in the target repo
