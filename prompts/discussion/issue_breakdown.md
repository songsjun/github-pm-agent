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

**AI-AGENT-FRIENDLY ISSUE RULES — apply to every issue:**

These rules directly determine whether a coding agent can successfully implement the issue. Follow them strictly.

**Rule 1 — Specify location.**
Every issue must name the exact file(s) and function(s) to modify, derived from the technical design. If the file does not exist yet, specify the path where it should be created. Never leave location implicit.

**Rule 2 — Describe behavior change, not intent.**
Forbidden verbs: "improve", "optimize", "refactor", "enhance", "consider". Each issue must describe a concrete before/after behavior change or a new function with explicit input→output contract.

**Rule 3 — Include a runnable acceptance test.**
Every issue body must end with a concrete test condition in code form: `assert func(input) == expected_output` or `GET /endpoint returns {"key": "value"}`. This is the single source of truth for "done".

**Rule 4 — Limit scope to 1–2 files.**
If implementing a feature requires touching more than 2 files, split it into multiple issues. Cross-file coordination is the #1 cause of agent failure.

**Rule 5 — Order by dependency.**
Issues must be listed in the order workers should implement them. Each issue may optionally reference which earlier issue it depends on.

---

Output ONLY a JSON array. No prose, no markdown fences, no explanation. Each item must have:
- "title": short imperative string (e.g. "Implement vocabulary flashcard exercise type")
- "body": structured markdown using the template below
- "labels": array of label strings (use: "enhancement", "bug", "documentation", "backend", "frontend", "infrastructure", "content", "test" as appropriate)

**Body template** (use this exact structure for every issue):
```
## What to change
<1 sentence: imperative verb + specific function/file + concrete behavior change>

## Location
- File: `path/to/file.py` (create if new)
- Function: `function_name()` (or "new module-level function")

## Current behavior
<What happens now, or "file/function does not exist yet">

## Expected behavior
<What must happen after this issue is implemented — be specific about return values, side effects, error cases>

## Acceptance test
<Runnable assertion or curl command that passes when done>
```

Example:
[
  {
    "title": "Add TokenExpiredError to auth/session.py validate_token()",
    "body": "## What to change\nRaise `TokenExpiredError` in `validate_token()` when the token's expiry timestamp is in the past.\n\n## Location\n- File: `auth/session.py`\n- Function: `validate_token(token: str) -> User`\n\n## Current behavior\nReturns `None` for expired tokens.\n\n## Expected behavior\nRaises `TokenExpiredError(token_id)` when `token.exp < time.time()`. Valid tokens still return a `User` object.\n\n## Acceptance test\n```python\nwith pytest.raises(TokenExpiredError):\n    validate_token(make_expired_token())\n```",
    "labels": ["backend", "enhancement"]
  }
]

Extract 6–12 issues, ordered by implementation dependency (foundational first). Issues must include **both implementation and test tasks**:
- Implementation issues: concrete feature or infrastructure changes (labeled "backend", "frontend", etc.)
- Test issues: one test issue per functional module (labeled "test"), covering unit tests, integration tests, or end-to-end flows

Checklist before outputting:
- [ ] Every issue body uses the required 5-section template
- [ ] Every issue names a specific file and function
- [ ] Every issue has a runnable acceptance test
- [ ] No single issue touches more than 2 files
- [ ] No forbidden verbs ("improve", "optimize", "refactor", "enhance", "consider") appear in titles or What-to-change lines
- [ ] At least 1/3 of issues cover domain/functional features (not just setup)
- [ ] At least 1 issue covers the primary user-facing feature from the PRD user stories
- [ ] At least 2 issues are test issues (labeled "test") covering key modules or flows
- [ ] Issues are ordered so that dependencies come before dependents (test issues after their implementation counterparts)
