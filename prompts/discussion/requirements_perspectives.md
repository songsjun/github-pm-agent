The PM has completed a brainstorm for a product idea. Now provide your role-specific input on requirements.

**Discussion:** $discussion_title

**Brainstorm summary:**
$artifact_brainstorm

$pending_comments

---

**SCOPE CALIBRATION — do this first, silently:**
The brainstorm above identified a scope. Match your requirements to that scope:
- Single-user personal tool → minimal auth, simple storage, no team workflows
- Small team internal tool → lightweight auth, shared storage, basic access control
- Public product → full auth, scalable storage, security hardening

Do not propose requirements that belong to a larger scope than what was identified.

---

Based on the brainstorm, identify requirements from your professional perspective using this template:

**Must-haves**
Non-negotiable requirements from your domain — things without which the product cannot function at the identified scope. Be specific, be minimal. (3-5 bullets)

**Nice-to-haves**
Requirements that improve quality but are not blockers. Flag clearly that these are optional. (2-3 bullets)

**Hard constraints**
Things that absolutely cannot be done or must be avoided given the scope, tech, or deployment context. (1-3 bullets)

**Dependencies**
External systems, APIs, or decisions that requirements depend on. Only include real dependencies for this scope — do not import enterprise dependencies that aren't needed. (1-3 bullets)

**Risk flags**
Requirements that look simple but hide significant complexity at the stated scope. (1-2 bullets max)

> ⚠️ If any AI/ML component is involved: Do not add requirements that assume AI output is deterministic, stable, or always correct. Requirements referencing AI must include fallback behavior.

Keep it under 300 words. Plain markdown, no JSON.
