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

**Approved technical design** — read this before writing any code. It defines the authoritative architecture decisions for this project: technology stack, data stores, module boundaries, and how components connect. Your implementation must be consistent with these decisions.
$repo_tech_design

**Project conventions** — framework patterns, error handling, auth resolution, test setup, file/module layout. Follow these exactly; do not invent alternative patterns.
$repo_conventions

**Current content of the target file** — if the file already exists in the repo, its full content is shown below. Preserve ALL existing exports and only ADD new ones. If shown as "does not exist yet", create the file from scratch.
$existing_file_contents

**Current content of dependency files** — these are the actual files this issue's file will import from. Use only the exports shown here; do not invent function signatures or types that aren't present.
$dependency_interfaces

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
- Choose an `install_command` if setup is required before testing. Derive the install sequence from `$repo_tech_design` — it specifies the tech stack, package manager, ORM, and any required environment setup steps.
- Set `branch_name` using the `ai/issue-{number}-{short-slug}` pattern.
- Set `commit_message` to a clear conventional commit message for this issue.
- **Bootstrap first**: If the repository lacks project setup files required to run tests (e.g., `package.json`, `tsconfig.json`, `pyproject.toml`, `requirements.txt`, `.env.example`), include them in `files`. Derive the correct setup from `$repo_tech_design`.
- **CRITICAL — Preserve existing exports**: If `$existing_file_contents` shows a non-empty file, your output MUST include ALL exports already present in that file, plus any new ones. Never remove or rename an existing export.
- **CRITICAL — Use only real dependency interfaces**: If `$dependency_interfaces` is provided, your imports from those files must use only the function/type names shown there. Do not invent signatures or types that aren't present in `$dependency_interfaces`.
- **CRITICAL — Do NOT regress existing dependencies**: If the repo already has a `package.json` or other dependency file, your new version MUST include ALL existing dependencies plus any new ones. Never remove a dependency that was already there.
- **CRITICAL — Do NOT create parallel in-memory stores for existing domain entities**: Before writing any `let store: X[] = [...]` pattern, check `$repo_tech_design` for the canonical data store for this entity. If a module already manages this entity via a database ORM, import and use it — do not invent a second, disconnected store.

Output ONLY the JSON block. No explanation before or after.
