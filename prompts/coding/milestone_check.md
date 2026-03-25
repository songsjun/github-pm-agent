You are doing a holistic product review after all implementation issues for a milestone have been merged. Your job is to verify that the implemented codebase delivers what the PRD promised — by reading actual code, not design documents.

**Project:** $discussion_title

**Product Requirements (PRD)** — committed from the approved requirements phase:
$repo_prd

**Approved technical design** — committed from the approved tech review phase:
$repo_tech_design

**Route files (main branch):**
$route_file_contents

**Library / service files (main branch):**
$lib_file_contents

$human_comment
$pending_comments

---

**ANALYSIS INSTRUCTIONS**

Do not reason about design intent. Only read what is actually present in the code above.

## Part 1 — PRD Feature Coverage

For each Must-have feature and user story in the PRD, find the implementation in the route and library files.

Output a table:

| # | PRD feature / user story | Priority | Implemented? | Evidence (file + function) | Gap |
|---|--------------------------|----------|--------------|---------------------------|-----|

**Implemented?** values:
- `Yes` — a route or function exists that directly delivers this feature
- `Partial` — a function exists but the calling chain is broken (e.g., function is defined but no route invokes it, or a route exists but a required dependency is missing)
- `No` — no route or function implements this feature at all

**Gap** (for Partial or No only): one sentence naming the specific missing piece — not a vague description, a concrete file/function/route that needs to exist.

---

## Part 2 — User Journey Walkthrough

For each primary user story in the PRD, trace the journey step by step through the actual route files.

For each step: does a route exist that handles it? Does it call the correct library function? Does the library function read from / write to the correct data store?

Output:

**Journey: [story title]**
| Step | Route | Handler function | Data store used | Status |
|------|-------|-----------------|-----------------|--------|

Status values: `Connected` / `Broken` / `Missing`

A journey is **Broken** if any step is Broken or Missing.

---

## Part 3 — Verdict

```
MILESTONE_STATUS: READY | GAPS_FOUND | CRITICAL_GAPS
```

- `READY` — all Must-have features are implemented and all primary user journeys are Connected end-to-end
- `GAPS_FOUND` — partial implementations exist; the product can partially run but some features are incomplete
- `CRITICAL_GAPS` — one or more Must-have features are entirely missing, or a primary user journey cannot be completed at all

After the verdict line, list the top 3 most important gaps (if any), each as one sentence naming the specific missing code.

---

Output plain markdown. This is a human-gated review — the owner will read it and decide whether to open additional issues, accept the gaps, or ship.
