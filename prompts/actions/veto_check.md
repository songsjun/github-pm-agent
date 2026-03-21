Event type: ${event_type}
Repository: ${repo}

Event payload:
${event_payload}

Task:
Decide whether this event should be blocked.
Only block when there is a concrete reason in the event details.
If there is not enough evidence to block, return false.

Output exactly JSON:
{"should_block": true/false, "reason": "..."}
