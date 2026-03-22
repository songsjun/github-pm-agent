Write a product requirements document (PRD) based on the brainstorm and team input below.

**Discussion:** $discussion_title

**Original idea:**
$discussion_body

**Brainstorm:**
$artifact_brainstorm

**Engineer's requirements input:**
$artifact_requirements_perspectives_engineer

**Security's requirements input:**
$artifact_requirements_perspectives_security

$human_comment
$pending_comments

---

**SCOPE CALIBRATION — do this first, silently:**
Extract the scope from the brainstorm (single-user / small team / public). Every section below must be written for that scope. If scope was not stated, assume the smallest plausible scope based on the idea.

Right-sizing rules:
- Single-user tool: no multi-tenant auth, no team roles, SQLite or file storage is acceptable, GitHub Pages or local server is acceptable
- Personal/internal tool: security requirements are about protecting the user's own data, not defending against external attackers at scale
- Do not add success metrics that require analytics infrastructure if the tool is for personal use (e.g., "DAU", "retention rate" are wrong for a personal tool)

---

Write a PRD using this template:

**Scope**
Target users and deployment context. (1 sentence — this anchors everything)

**Problem statement**
What pain does this solve for those users? (1 paragraph)

**Goals**
What success looks like at the identified scope. (2-4 bullets, must be achievable without adding scope)

**Non-goals**
What is explicitly out of scope. Include patterns that might be tempting but don't match the scale (e.g. "multi-user support is out of scope", "no analytics dashboard"). (3-5 bullets)

**User stories**
Key stories in "As a [user], I want [feature], so that [benefit]" format. (3-5 stories, matched to the identified user)

**Technical constraints**
Hard constraints from the engineer — infrastructure, compatibility, stated platform preferences. (2-4 bullets)

**Security requirements**
Security needs matched to scope. Personal tool: protect local data, avoid data leaks. Public tool: auth hardening, rate limiting, etc. Do not apply public-SaaS security to personal tools. (2-4 bullets)

**Success metrics**
How the owner will know this is working. For personal tools, qualitative metrics are fine (e.g., "I use it daily", "my score improved over 4 weeks"). Do not require analytics infrastructure. (2-3 metrics)

**Open questions**
Unresolved decisions that need answers before building. (2-4 questions)

> ⚠️ If any AI/ML component is involved: Do not add requirements that assume AI output is always correct or stable. Requirements referencing AI must include graceful fallback behavior.

Plain markdown, no JSON.
