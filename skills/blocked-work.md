# Blocked Work Skill

Use this skill when work has stalled: blocked issues, reopened items, or workflow failures that need investigation.

Goal:

- replace vague status with an actionable unblock plan
- force ownership, next step, and next update time
- distinguish observation from root-cause evidence

Require these elements:

- blocker or failure description
- current hypothesis, if root cause is not proven yet
- owner of the next action
- next concrete step
- expected next update time

Expected output:

- one concise status request or follow-up issue body
- no more than one main ask per interaction

Guardrails:

- “investigating” alone is not enough
- do not close or downgrade a blocker without evidence
- if the event is noisy but low-impact, prefer memory-only observation
