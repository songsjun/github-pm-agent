You are preparing a system component inventory before implementation tasks are written. Your output becomes the coverage constraint for the issue breakdown that follows.

**Discussion:** $discussion_title

**Product Requirements:**
$artifact_requirements

**Final Technical Design:**
$artifact_final_design

$pending_comments

---

Read the Final Technical Design and produce a structured component inventory. Do not summarize — enumerate every distinct component explicitly.

**Output a markdown table:**

| # | Layer | Component | Called by | Calls into | MVP? |
|---|-------|-----------|-----------|------------|------|

Use exactly these layer values:
- `ui` — pages, forms, components the user directly interacts with
- `api` — HTTP route handlers (GET / POST / PUT / DELETE endpoints)
- `service` — business logic, domain operations, helpers called by API routes
- `adapter` — external integrations (AI providers, file storage, CLI tools, external APIs)
- `data` — database models, Prisma/ORM schema, migration helpers
- `pipeline` — orchestrators, triggers, or workers that connect multiple steps end-to-end

**Enumeration rules:**

1. **Pipeline rows are mandatory for every multi-step flow.** For each flow described in the design (e.g. "save raw file → parse/OCR → validate quality → invoke AI diagnosis → write result with status"), create a `pipeline` row whose component name is the orchestrator or trigger — even if the design does not name it explicitly. Ask: "what code calls step B after step A completes, and where does it live?" If no answer exists in the design, mark it `pipeline | [unnamed trigger for X flow] | unknown | ... | ?` so the owner can decide.

2. **UI rows are mandatory if the design mentions any pages, forms, or a dashboard.** List each distinct user-facing screen separately. If the design explicitly defers all UI to a later version, mark one row `ui | [all frontend] | — | — | No (deferred per PRD)`.

3. **Do not collapse multiple components into one row** just because they share a file. Each callable unit that could be a separate issue gets its own row.

4. **MVP? column:** Write `Yes`, `No — [reason]`, or `? — owner decides`. The reason must reference a specific PRD non-goal or explicit MVP boundary statement. "Too complex" is not a valid reason.

---

After the table, output two short sections:

**Deferral summary**
List every "No" row in one sentence each, stating why it is out of MVP scope.

**Uncertain components**
List any component you suspect exists but could not find explicit evidence for in the technical design. The owner must confirm whether these are in scope before issue breakdown begins.

---

After the table and the two sections above, append a final section titled exactly:

## Project Conventions

Derive from the Final Technical Design a concise reference for coding agents. Cover ONLY what is explicitly stated or clearly implied by the tech design — do not invent conventions. Use bullet points. Sections:

**Stack & runtime**
- Language, framework, runtime version (e.g. Next.js 14, Node 20, TypeScript 5)
- Package manager (npm / pnpm / yarn)

**Data layer**
- ORM and database (e.g. Prisma + SQLite, SQLAlchemy + PostgreSQL)
- Schema file location and migration command
- How to get the DB client in service files

**Authentication**
- How auth is resolved in routes (e.g. `getServerSession()`, JWT middleware, cookie)
- What shape the current user object has (fields, nullability)

**Error handling**
- Expected HTTP status codes for common cases (not-found, unauthorized, bad input)
- Whether routes throw or return error objects
- Any error wrapper utility mentioned in the design

**Testing**
- Test runner and config file location
- How to run all tests / a single test file
- Any required env setup before tests (e.g. `cp .env.example .env && npx prisma db push`)

**Environment**
- Required env vars and where they are documented (`.env.example`)
- Any env var naming convention

**File & module conventions**
- Where types/interfaces live (e.g. `src/types/`)
- Where service functions live (e.g. `src/lib/`)
- Where route handlers live (e.g. `src/app/api/`)
- Any barrel export pattern or index file convention

Leave out any section for which the tech design provides no information. Coding agents and reviewers will use this section as a quick-reference card — keep it scannable, not exhaustive.

---

Output plain markdown. No JSON. This comment will be reviewed by the owner, who will confirm, add missing components, or mark additional deferrals before implementation tasks are written.
