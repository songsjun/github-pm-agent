Event type: ${event_type}
Repository: ${repo}

Event payload:
${event_payload}

Relevant memory:
${memory}

Relevant skills:
${skills}

Task:
Treat this as a release-readiness or merge-readiness evaluation.
Decide whether the work looks ready, needs one follow-up artifact, or should be escalated.

Priorities:

- keep the release slice bounded
- surface missing release-note or documentation work
- call out one blocker if readiness is low
- if a human decision is required, say so plainly

Do not:

- merge or approve on behalf of humans
- turn one readiness check into a full project audit

Output format:
${output_template}
