Based on the product requirements below, extract a list of implementation tasks as GitHub issues.

**Discussion:** $discussion_title

**Requirements:**
$artifact_requirements

$pending_comments
---

Output ONLY a JSON array. No prose, no markdown fences, no explanation. Each item must have:
- "title": short imperative string (e.g. "Add user authentication endpoint")
- "body": 2-4 sentence description with acceptance criteria
- "labels": array of label strings (use: "enhancement", "bug", "documentation", "backend", "frontend" as appropriate)

Example format:
[
  {"title": "...", "body": "...", "labels": ["enhancement"]},
  {"title": "...", "body": "...", "labels": ["backend", "enhancement"]}
]

Extract 3-8 issues. Prioritize concrete, implementable tasks.
