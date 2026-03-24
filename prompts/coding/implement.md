You are implementing GitHub issue #$issue_number in repo $repo.

Issue title: $issue_title

Issue body:
$issue_body

Repository context:
- Default branch: $default_branch
- Base branch: $base_branch

Additional context:
- Previous worker analysis comments, if any:
$pending_comments
- Human comment, if any:
$human_comment

If `$pending_comments` is non-empty, incorporate useful prior analysis.
If `$human_comment` is provided, follow it unless it conflicts with the issue requirements.
If `$test_failure_context` is set, analyze the failure details below, fix the underlying issue, and update any affected files or commands as needed:
$test_failure_context

Return a single JSON block in ```json ... ``` with this exact schema:
```json
{
  "files": [
    {"path": "relative/path/to/file.py", "content": "...complete file content..."}
  ],
  "test_command": "pytest tests/ -v",
  "install_command": "pip install -e .",
  "branch_name": "ai/issue-{number}-{short-slug}",
  "commit_message": "feat: implement X for issue #{number}"
}
```

Requirements:
- Include ALL files that need to change, whether new or modified.
- For every entry in `files`, provide the FULL file content, not diffs.
- Use repository-relative file paths.
- Choose a `test_command` that validates the change.
- Choose an `install_command` if setup is required before testing.
- Set `branch_name` using the `ai/issue-{number}-{short-slug}` pattern.
- Set `commit_message` to a clear conventional commit message for this issue.
- **Bootstrap first**: If the repository lacks project setup files required to run tests (e.g., `package.json`, `tsconfig.json`, `pyproject.toml`, `requirements.txt`, `jest.config.js`, `.env.example`), include them in `files` with appropriate content. Never assume they already exist — always generate them if missing.
- For TypeScript/Node projects: generate `package.json` (with name, version, scripts.test, devDependencies including jest/ts-jest/typescript), `tsconfig.json`, and any jest config needed to run the test command.
- For Python projects: generate `pyproject.toml` or `requirements.txt` if missing.
- The `install_command` must install everything the `test_command` needs.
- **CRITICAL — Do NOT regress existing dependencies**: If the repo already has a `package.json` or other dependency file merged, your new version MUST include ALL existing dependencies plus any new ones required by this issue. Never remove a dependency that was already there.
- **This repo already has Prisma set up**: The main branch already contains `@prisma/client ^6` and `prisma ^6` in package.json. Any new `package.json` you generate MUST include these. The `install_command` MUST be `npm install && npx prisma generate` for this repo.
- **Single Jest config**: Do NOT create both `jest.config.js` and `jest.config.cjs` — use one (`jest.config.cjs` is preferred for CommonJS). If `jest.config.cjs` already exists, do not also create `jest.config.js`.
- **Failing pre-existing tests**: If `$test_failure_context` shows a test file you did NOT create (e.g. `tests/goals.test.ts`) failing with `Cannot find module '@prisma/client'`, it means your package.json is missing `@prisma/client`. Add `"@prisma/client": "^6.0.0"` to dependencies and `npx prisma generate` to install_command.
- **CRITICAL — Do NOT remove existing exports**: This repo is built incrementally — earlier issues already merged `getActiveStudyGoal()` into `src/lib/goals.ts`, Prisma models into `prisma/schema.prisma`, etc. If the issue body says "Create `src/lib/goals.ts`" but that file already exists with exports, you MUST preserve ALL existing exports (functions, types, constants) and only ADD new ones. Never overwrite a merged file with a version that drops existing exports — the pre-existing test files import those exports and will fail if they disappear.
- **CRITICAL — Do NOT remove existing Prisma models**: `prisma/schema.prisma` already has a `StudyGoal` model merged from a previous issue. Any new `prisma/schema.prisma` you generate MUST include ALL existing models plus new ones. Dropping an existing model causes TypeScript errors like `Property 'studyGoal' does not exist on type 'PrismaClient'`.
- **CRITICAL — getActiveStudyGoal must be async**: `src/lib/goals.ts` was already merged with an `async getActiveStudyGoal()` function that returns `Promise<...>`. If you write a new version of this file, the function MUST remain `async` and return a Promise. Synchronous versions cause `Matcher error: received value must be a promise` in the test suite.
- **CRITICAL — SQLite does NOT support Json type**: Do NOT use `Json` or `Json?` field types in `prisma/schema.prisma` when the datasource provider is `sqlite`. SQLite Prisma does not support the `Json` scalar — using it causes `npx prisma generate` to fail entirely, which means NO model types are generated and every model access fails with `Property 'X' does not exist on type 'PrismaClient'`. Use `String` instead for any JSON-like fields (serialize/deserialize in application code).

Output ONLY the JSON block. No explanation before or after.
