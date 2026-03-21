Event type: ${event_type}
Repository: ${repo}

Event payload:
${event_payload}

Relevant memory:
${memory}

Relevant skills:
${skills}

Task:
Treat this as blocked or failing work that needs investigation discipline.
Produce one bounded PM action that makes the next unblock step explicit.

Priorities:

- separate known facts from hypotheses
- require owner, next step, and next update time
- prefer a concise unblock comment or follow-up issue over broad advice
- if the event is informative but not actionable, keep it as observation only

Do not:

- assume root cause without evidence
- post a noisy “please investigate” comment with no structure

Output format:
${output_template}
