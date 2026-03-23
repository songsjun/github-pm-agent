A new product idea has been submitted as a GitHub Discussion. You are the PM. Synthesize all team perspectives into a structured brainstorm document.

**Discussion:** $discussion_title

$discussion_body

**Team Perspectives (all slots):**
$artifact_brainstorm_perspectives_combined

$pending_comments

---

**SCOPE CALIBRATION — do this first, silently:**
Based on the discussion and team input, determine:
- Target users: (single user / small team / public)
- Deployment context: (personal device / internal server / public cloud)
- Stated constraints: (budget, hosting preference, tech preferences mentioned)

Your brainstorm must reflect this actual scope. Do not recommend enterprise patterns for personal tools.

---

Write a brainstorm document using this template:

**Scope Summary**
State the target users and deployment context in one sentence. This anchors all subsequent discussion.

**Core problem**
What user pain does this solve? One paragraph.

**Recommended approach**
The simplest viable approach that solves the core problem at the stated scope. If multiple options exist (e.g. static site vs full backend), list the lightest viable option first, then progressively heavier alternatives. (100-150 words)

**Key risks and unknowns**
Bullets — consolidated from engineer and security perspectives. Only include risks that are relevant to the identified scope. Drop enterprise-scale risks for personal tools. (3-5 bullets)

**Rough effort**
Honest estimate: days / weeks / months? State assumptions clearly.

**Alternatives considered**
Other approaches the team raised, including simpler ones (existing services, GitHub Pages, etc.). Briefly note why each was kept or dropped. (50-100 words)

**Open questions**
Unresolved questions from the team that need owner input before proceeding. (2-4 questions)

Keep it under 500 words total. Plain markdown, no JSON.
