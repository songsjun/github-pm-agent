# Review Readiness Skill

Use this skill when a pull request is active but review progress is noisy, stalled, or unclear.

Goal:

- turn review friction into one bounded next step
- keep review requests specific and procedural
- distinguish review-ready work from underdefined work

Check for:

- whether the PR goal is still clear
- whether the author needs to address one blocking comment first
- whether the right reviewer is engaged
- whether the PR should stay in review or return to scope clarification

Expected output:

- one short readiness status: `ready`, `needs author action`, or `underdefined`
- one concrete next step
- one escalation note if the decision is product- or architecture-defining

Guardrails:

- do not re-litigate the whole PR
- do not fabricate approval confidence
- if review noise hides a scope problem, hand off to `scope-guard`
