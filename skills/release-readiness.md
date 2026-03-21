# Release Readiness Skill

Use this skill when a PR, milestone, or merge/release boundary needs a final PM check.

Goal:

- confirm the work is ready to land without introducing avoidable ambiguity
- catch documentation drift and missing rollout communication

Check for:

- scope is still the intended slice
- CI or validation status is not obviously blocking
- release note or changelog impact is understood
- any user-facing docs, migration notes, or follow-up issues are accounted for

Expected output:

- readiness status: `ready`, `needs follow-up`, or `not ready`
- one blocker if not ready
- one follow-up artifact if docs or release notes are missing

Guardrails:

- do not merge or release autonomously
- if the decision affects product promise or rollout timing, escalate to a human
