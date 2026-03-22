Based on the approved product requirements and final technical design below, extract a list of concrete implementation tasks as GitHub issues.

**Discussion:** $discussion_title

**Product Requirements:**
$artifact_requirements

**Final Technical Design:**
$artifact_final_design

$human_comment
$pending_comments

---

**SCOPE CALIBRATION — do this first, silently:**
Review the PRD scope. Issues must:
1. Match the actual architecture (e.g., if SQLite was chosen, do not create issues for PostgreSQL migrations)
2. Cover the **full product**, not just infrastructure — include domain-specific functionality issues
3. Be completable at the stated scope — no issue should require skills or infrastructure beyond what the PRD describes

**Balance rule**: At least 1/3 of issues must cover domain/functional features (the actual user-facing value), not infrastructure setup. For example, a French learning tool needs issues for content types, exercise logic, and learning flow — not just "set up database" and "configure Docker".

---

Output ONLY a JSON array. No prose, no markdown fences, no explanation. Each item must have:
- "title": short imperative string (e.g. "Implement vocabulary flashcard exercise type")
- "body": 2-4 sentence description with acceptance criteria grounded in the technical design and PRD user stories
- "labels": array of label strings (use: "enhancement", "bug", "documentation", "backend", "frontend", "infrastructure", "content" as appropriate)

Example format:
[
  {"title": "...", "body": "...", "labels": ["enhancement", "frontend"]},
  {"title": "...", "body": "...", "labels": ["backend", "enhancement"]}
]

Extract 4-8 issues. Checklist before outputting:
- [ ] At least 1 issue covers core domain functionality (not just setup/infrastructure)
- [ ] At least 1 issue covers the primary user-facing feature from the user stories
- [ ] Each issue maps to a specific requirement or component in the technical design
- [ ] No issue references architecture that was not in the final design
- [ ] Issue scope matches stated deployment (e.g., no cloud services if running locally)
