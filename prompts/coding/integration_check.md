You are verifying that a completed batch of issues produces a working end-to-end product by statically tracing import connections across route files.

**Primary user journeys from the PRD:**
$repo_prd$artifact_requirements

**Route files and their content:**
$route_file_contents

**Library files and their content:**
$lib_file_contents

---

**ANALYSIS INSTRUCTIONS**

Do not reason about runtime behavior. Only trace static imports.

For each route file (files under `src/app/api/` or equivalent):

1. List every `import` statement.
2. For each imported function that touches a domain entity (goals, submissions, analysis results, users, etc.), identify which module it comes from and what backing store that module uses (Prisma, in-memory array, external API, etc.).
3. If two route files that handle the same domain entity import from **different modules with different backing stores**, mark that as a DATA SOURCE SPLIT.

Then, for each user journey listed in the PRD:

1. Trace the journey step by step through the route files.
2. At each step, check whether the data produced by that step is readable by the next step (i.e., both steps use the same backing store for the shared entity).
3. Assign status:
   - **COMPLETE** — every step connects; data flows through without store switches
   - **BROKEN** — at least one step writes to store A while the next step reads from store B
   - **PARTIAL** — the step exists but invokes a library function that is never called by any route (dead export)
   - **MISSING** — a required step has no route or library function implementing it

**Output format (JSON only, no prose):**

```json
{
  "data_source_splits": [
    {
      "entity": "StudyGoal",
      "route_a": "src/app/api/goals/route.ts",
      "store_a": "Prisma via src/lib/goals.ts",
      "route_b": "src/app/api/submissions/route.ts",
      "store_b": "in-memory array in src/lib/submissions.ts",
      "severity": "blocking"
    }
  ],
  "dead_exports": [
    {
      "file": "src/lib/ai/adapter.ts",
      "export": "runAnalysis()",
      "called_by": [],
      "severity": "blocking"
    }
  ],
  "journeys": [
    {
      "journey": "Student creates goal → submits material → views dashboard",
      "steps": [
        {"step": "POST /api/goals", "status": "COMPLETE", "note": ""},
        {"step": "POST /api/submissions with returned goal id", "status": "BROKEN", "note": "submissions.ts validates against in-memory store, not Prisma"},
        {"step": "GET /api/dashboard", "status": "PARTIAL", "note": "route exists but AnalysisResult table is never populated"}
      ],
      "overall": "BROKEN"
    }
  ],
  "verdict": "INCOMPLETE",
  "required_fixes": [
    "submissions/route.ts must validate studyGoalId against Prisma (via goals.ts getActiveStudyGoal) instead of the in-memory studyGoals array",
    "Add a POST /api/submissions/:id/analyze route that calls runAnalysis() from src/lib/ai/adapter.ts and writes an AnalysisResult record"
  ]
}
```

If all journeys are COMPLETE and there are no data source splits or dead exports, output `"verdict": "COMPLETE"` and empty arrays for the other fields.

Output ONLY the JSON block.
