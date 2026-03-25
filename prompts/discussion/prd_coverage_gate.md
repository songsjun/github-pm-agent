You are cross-referencing the product requirements against the technical proposals to surface any features that were dropped, narrowed, or left unaddressed before the final design is written.

**Discussion:** $discussion_title

**Product Requirements (PRD):**
$artifact_requirements

**Technical Proposals:**
$all_tech_proposals

$pending_comments

---

Read the PRD and the technical proposals carefully. Produce a coverage table that maps every PRD requirement to its treatment in the technical proposals.

**Output a markdown table:**

| # | Feature / Requirement | PRD priority | Tech proposal coverage | Coverage verdict | Gap decision needed |
|---|----------------------|--------------|----------------------|-----------------|---------------------|

Column definitions:

- **Feature / Requirement**: one row per distinct feature, user story, or technical constraint from the PRD. Extract from: Must-haves, Should-haves, User stories, Technical constraints, Security requirements. Do not collapse multiple distinct features into one row.
- **PRD priority**: `Must` / `Should` / `Could` / `Constraint` (use the MoSCoW labels from the PRD; if unlabeled, infer from context)
- **Tech proposal coverage**: quote the specific section or sentence from the proposals that addresses this feature. If nothing addresses it, write `—`.
- **Coverage verdict**: one of:
  - `Full` — proposal addresses it completely
  - `Partial` — proposal mentions it but leaves implementation specifics undefined (e.g. "we will handle this later", risk flag without mitigation plan)
  - `Narrowed` — proposal addresses a reduced version of what the PRD requires (scope cut without explicit justification)
  - `Missing` — no mention in any proposal
- **Gap decision needed**: for `Partial`, `Narrowed`, or `Missing` rows only. Write one of:
  - `Engineer must address in final design` — the gap is in scope and should be filled before coding begins
  - `Owner decision: defer to post-MVP?` — the gap may be an intentional MVP cut but needs explicit owner sign-off
  - `—` (for `Full` rows)

---

After the table, output two sections:

**Summary**
One sentence per gap row (Partial / Narrowed / Missing), stating what the owner or engineer must resolve.

**Scope narrowings to confirm**
List every case where the tech proposal silently narrowed a PRD feature without calling it out (Coverage verdict = `Narrowed`). These are the highest-risk rows — silent scope cuts that neither the owner nor engineer may have noticed. Each entry should name the PRD feature and the narrower version in the proposal.

---

Output plain markdown. No JSON. This comment will be reviewed by the owner, who will confirm each gap decision before the final technical design is written.
