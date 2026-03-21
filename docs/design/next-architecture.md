# Next Architecture

## Goal

Evolve `github-pm-agent` from an event reactor into a staged PM workflow runtime without replacing its MVP shape.

## Current Shape

```text
poller -> queue -> engine -> handler -> ai adapter -> actions -> memory
```

This is still the right base. The weakness is not the high-level architecture; it is the thinness of the decision layer between `handler` and `ai/actions`.

## Proposed Incremental Shape

```text
poller
  -> queue
  -> lifecycle router
  -> handler
  -> prompt/skill selection
  -> ai/action planning
  -> bounded execution
  -> artifact + memory distill
```

## Main Gaps And Improvements

| Area | Current gap | Incremental improvement |
|---|---|---|
| Routing | mostly raw `event_type` routing | add lifecycle stage and risk classification |
| Prompts | generic fallback prompt is overloaded | stage-specific prompts for intake, spec, blocker, review, release, retro |
| Skills | one PM core skill | split into focused policy skills |
| Escalation | typed metadata now exists, but no policy engine yet | add stage-aware auto vs human-required decisions |
| Artifacts | file-backed brief/spec/release/retro artifacts exist | feed them more selectively and generate richer artifacts |
| Control plane | single-repo poll/cycle only in the first cut | runtime now exposes `poll`, `reconcile`, `daemon`, `webhook`, and `analytics` |
| Validation | code tests exist, prompt/skill drift tests do not | add inventory and routing validation tests |

## Minimal New Components

### 1. Lifecycle Router

Purpose:

- classify events into lifecycle stages
- select the best prompt and skill set
- keep deterministic handlers intact

Desired outputs:

- `stage`
- `prompt_path`
- `skill_refs`
- `risk_level`
- `requires_human`

### 2. Artifact Layer

Purpose:

- persist reusable outputs instead of only comments and memory notes

Initial artifact types:

- `brief`
- `spec-review`
- `release-readiness`
- `retro-summary`

Storage can remain local-file based in the MVP.
This is now implemented with a file-backed runtime artifact store.

### 3. Typed Escalation

Purpose:

- tell the runtime when to stop and summarize rather than act

Initial fields worth adding later:

- `needs_human_decision`
- `evidence`
- `options`
- `follow_up_after`
- `cooldown_key`

The first four are now implemented in the action-plan contract, except `cooldown_key`.

## What Should Stay Simple

- keep single-process execution
- keep JSONL queue and local runtime state
- keep `gh api` as the GitHub integration surface
- keep deterministic actions narrow and auditable

## What Should Not Be Added Yet

- a database
- a vector store
- concurrent workers
- autonomous merge/release behavior
- browser-driven product QA

## Near-Term Architecture Plan

| Phase | Focus | Expected code impact |
|---|---|---|
| Phase 1 | docs + prompt/skill structure | completed |
| Phase 2 | lifecycle routing | completed |
| Phase 3 | validation and artifact tests | completed |
| Phase 4 | richer escalation and policy control | engine, templates, selected handlers, runtime control |

## Success Metrics

- fewer events hit the generic fallback path
- more comments are stage-appropriate and less repetitive
- unsupported events/actions are visible in TODO, not hidden in code
- new skills/prompts can be added without touching core runtime everywhere
