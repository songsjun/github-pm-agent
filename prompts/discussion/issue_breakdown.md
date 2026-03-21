Based on the approved product requirements and final technical design below, extract a list of concrete implementation tasks as GitHub issues.

**Discussion:** $discussion_title

**Product Requirements:**
$artifact_requirements

**Final Technical Design:**
$artifact_final_design

$human_comment
$pending_comments
---

Output ONLY a JSON array. No prose, no markdown fences, no explanation. Each item must have:
- "title": short imperative string (e.g. "Add user authentication endpoint")
- "body": 2-4 sentence description with acceptance criteria grounded in the technical design
- "labels": array of label strings (use: "enhancement", "bug", "documentation", "backend", "frontend" as appropriate)

Example format:
[
  {"title": "...", "body": "...", "labels": ["enhancement"]},
  {"title": "...", "body": "...", "labels": ["backend", "enhancement"]}
]

Extract 3-8 issues. Each issue must map to a specific component or decision from the technical design.
