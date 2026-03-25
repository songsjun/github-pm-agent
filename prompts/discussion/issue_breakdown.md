Based on the approved product requirements and final technical design below, decompose the project into a small set of implementation tasks — one task per logical ownership boundary, the way an architect assigns work to a small dev team.

**Discussion:** $discussion_title

**Product Requirements:**
$artifact_requirements

**Final Technical Design:**
$artifact_final_design

$human_comment
$pending_comments

---

**STEP 1 — COVERAGE MAP (show your work before generating any issues):**

Check `$pending_comments` for a system component inventory from the coverage gate. If present, use its table as your coverage constraint.

If no inventory is present, derive one now from the Final Technical Design:

| Domain / Module | Core capability | Issue(s) that implement it — or "Deferred: [PRD reference]" |
|-----------------|----------------|--------------------------------------------------------------|
| (list every named domain: auth, goals, submissions, AI analysis, dashboard, etc.) | | |

Complete this table before writing a single issue. Every row without an issue assignment is a gap.

---

**TASK DECOMPOSITION MODEL — three issue types only:**

Think like an architect assigning sprints to a 3-5 person dev team. Decompose vertically (by domain/capability), not horizontally (by technical layer).

| Type | What it covers | Who it resembles |
|------|----------------|-----------------|
| `feature_module` | One complete domain: its types, schema, service functions, route handlers, and its own unit tests. An agent owns this domain end-to-end. | A full-stack developer assigned "the Goals module" |
| `integration` | Wires multiple feature modules together: app setup, middleware, pipeline triggers, orchestrators, event handlers. | A lead developer writing the glue code that makes modules talk to each other |
| `e2e_test` | Validates one or more complete user journeys by calling the live API in sequence, using only values returned by prior steps. | A QA engineer running acceptance tests after feature branches are merged |

**Decomposition rules:**

1. **Vertical ownership, not horizontal layers.** A `feature_module` for "Goals" covers `src/types/goal.ts` + `src/lib/goals.ts` + `src/app/api/goals/route.ts` in one issue — not three separate layer issues. Horizontal splitting destroys agent context.

2. **One agent, one coherent scope.** Each issue must be implementable by a single agent with full context, in one coding session, without needing to coordinate with another agent mid-implementation.

3. **Size guidance.** A `feature_module` should be ~100–500 lines total across all its files. If a domain is too large (e.g. an AI adapter with complex retry logic + two routes + its types), split it into two feature_modules at a natural boundary. `integration` issues may be smaller (~50–200 lines). Each `e2e_test` is one test file (~100–200 lines).

4. **Dependency order.** Issues must be listed in the order they should be implemented. `feature_module` issues come first (ordered by dependency: if module B imports from module A, module A's issue comes first). `integration` issues come after the modules they wire. `e2e_test` issues come last.

5. **Pipeline triggers are mandatory.** For every multi-step flow in the design (ingest → process → store → notify), there must be an `integration` issue that implements the trigger/orchestrator. A module function that is never called is a dead export.

6. **Explicit produces-for declarations.** Every `feature_module` must declare what it exports that other modules depend on. This is what the next agent needs to know to stay compatible.

---

**ISSUE RULES — apply to every issue:**

**Rule 1 — Name every file.** The `## Files` section must list every file this issue creates or modifies, with a one-phrase description of what each file contains.

**Rule 2 — Describe behavior, not intent.** Forbidden verbs: "improve", "optimize", "refactor", "enhance", "consider". Describe concrete input→output contracts.

**Rule 3 — Runnable acceptance test.** The `## Acceptance test` must be executable — curl commands or code assertions in sequence. No prose summaries.

**Rule 4 — Chain your test data.** If step B uses the ID produced by step A, the test must capture and chain the value — never hardcode fixture IDs.

Bad:  `POST /submissions {"goalId": "goal_1"}` → 201
Good: `goal = POST /goals {...}` → 201; `POST /submissions {"goalId": goal.id}` → 201

**Rule 5 — Declare what you produce.** The `## Produces for` section must name the types/functions exported by this module that other issues depend on. This is the integration contract downstream agents will rely on.

---

Output ONLY a JSON array. No prose, no markdown fences, no explanation. Each item must have:
- `"title"`: short imperative string
- `"body"`: structured markdown using the template below
- `"labels"`: array of label strings (`"enhancement"`, `"bug"`, `"backend"`, `"frontend"`, `"infrastructure"`, `"test"` as appropriate)
- `"type"`: one of `"feature_module"` | `"integration"` | `"e2e_test"`
- `"file_paths"`: array of ALL repo-relative file paths this issue creates or modifies (e.g. `["src/types/goal.ts", "src/lib/goals.ts", "src/app/api/goals/route.ts"]`)
- `"depends_on"`: array of repo-relative file paths this issue's files import from that are owned by OTHER issues (e.g. `["src/lib/db.ts", "src/lib/auth.ts"]`) — empty array `[]` if none

**Body template** (use this exact structure for every issue):

```
## What to implement
<1–3 sentences: complete capability this issue delivers, and which user stories it enables>

## Type
feature_module | integration | e2e_test

## Files
- `path/to/file.ts` (create) — what this file contains
- `path/to/file2.ts` (create) — what this file contains
(list every file, including test files owned by this issue)

## Depends on
- `path/to/dep.ts` — which function/type is needed from here
(write "None" if this issue has no external file dependencies)

## Produces for
- `downstream-module` needs: `TypeName`, `functionName()`
(list every export that a later issue will import; write "None" if this is a leaf module)

## Size estimate
~N lines total across all files

## Acceptance criteria
<What the user can do after this issue is merged — written as user-facing outcomes>

## Acceptance test
<Executable: curl commands or code assertions, chaining values from prior steps>
```

Example:
```json
[
  {
    "title": "Implement Goals module — types, service, and API route",
    "body": "## What to implement\nCreate the Goals domain: TypeScript types, Prisma schema, service functions, and POST/GET route handlers. Enables users to create and retrieve study goals.\n\n## Type\nfeature_module\n\n## Files\n- `src/types/goal.ts` (create) — StudyGoal interface, GoalId type\n- `src/lib/goals.ts` (create) — createGoal(), getGoalById(), listGoalsByUser()\n- `src/app/api/goals/route.ts` (create) — POST /api/goals, GET /api/goals\n- `src/app/api/goals/[id]/route.ts` (create) — GET /api/goals/:id\n\n## Depends on\n- `src/lib/db.ts` — Prisma client (prisma)\n- `src/lib/auth.ts` — getCurrentUser() returns User | null\n\n## Produces for\n- Submissions module needs: `StudyGoal` type, `getGoalById(id)`\n- Integration issue needs: `POST /api/goals` endpoint exists\n\n## Size estimate\n~220 lines total (types ~25, service ~90, routes ~105)\n\n## Acceptance criteria\nUser can create a study goal via POST /api/goals and retrieve it by ID or list all their goals.\n\n## Acceptance test\n```\ngoal = POST /api/goals {\"subject\": \"Math\", \"description\": \"Calc exam prep\"}\n→ 201 {id, subject, userId, createdAt}\n\nGET /api/goals/:goal.id  → 200 {id: goal.id, subject: \"Math\"}\nGET /api/goals           → 200 [{id: goal.id, ...}]\nGET /api/goals/nonexist  → 404\n```",
    "labels": ["backend", "enhancement"],
    "type": "feature_module",
    "file_paths": ["src/types/goal.ts", "src/lib/goals.ts", "src/app/api/goals/route.ts", "src/app/api/goals/[id]/route.ts"],
    "depends_on": ["src/lib/db.ts", "src/lib/auth.ts"]
  }
]
```

Generate **6–12 issues** total, ordered by implementation dependency. The breakdown should be:
- 4–8 `feature_module` issues covering all domains in the coverage map
- 1–2 `integration` issues wiring the modules (include any pipeline triggers, app middleware, or cross-module orchestrators)
- 1 `e2e_test` issue as the final item, covering the complete primary user journey end-to-end

Checklist before outputting:
- [ ] Every issue body uses the exact 8-section template
- [ ] Every `## Files` section lists all files with descriptions (no implicit files)
- [ ] Every `## Depends on` section is filled (or says "None")
- [ ] Every `## Produces for` section is filled (or says "None")
- [ ] Every issue has an executable acceptance test with chained values (no hardcoded IDs)
- [ ] Coverage map from Step 1 has no unresolved rows
- [ ] Every multi-step pipeline in the tech design has a corresponding `integration` issue
- [ ] Issues are ordered so all dependencies come before their dependents
- [ ] No `depends_on` references a file not owned by an earlier issue
- [ ] The final issue is `e2e_test` and covers the complete primary user journey
- [ ] JSON fields `type`, `file_paths`, `depends_on` are present on every item and match the body
- [ ] No forbidden verbs appear anywhere in titles or What-to-implement sections
