# Scope Guard Skill

Use this skill when work seems larger, fuzzier, or riskier than the current event suggests.

Goal:

- protect the smallest shippable slice
- surface spec gaps before execution drifts
- judge whether work is review-ready or still underdefined

Check for:

- clear goal and out-of-scope line
- reuse of existing behavior instead of parallel design
- hidden dependencies or rollout risk
- test impact and verification path
- whether the current PR/issue is actually ready for review

Expected output:

- one sentence on scope status: `tight`, `stretching`, or `unclear`
- the single most important missing detail
- the smallest next step that restores confidence

Guardrails:

- do not expand scope to “make it complete”
- prefer one blocker over a long audit list
- escalate if the missing piece changes product behavior or architecture
