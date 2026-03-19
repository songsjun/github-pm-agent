Event type: ${event_type}
Repository: ${repo}

Event payload:
${event_payload}

Relevant memory:
${memory}

Relevant skills:
${skills}

Task:
Treat this as a review-readiness event.
Decide the smallest PM action that helps review move forward without adding noise.

Priorities:

- identify whether the PR is review-ready, waiting on the author, or still underdefined
- keep the next step procedural and concrete
- if there is one clear blocker, center the response on that blocker
- escalate if the disagreement is really about product scope or architecture

Do not:

- restate the entire review thread
- pretend the PR is ready if the event suggests otherwise
- post a broad “please review” comment with no direction

Output format:
${output_template}
