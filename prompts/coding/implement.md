You are implementing GitHub issue #$issue_number in repo $repo.

Issue title: $issue_title

Issue body:
$issue_body

Project-level context pack:
$project_context_pack

Repository context:
- Default branch: $default_branch
- Base branch: $base_branch
- Issue labels: $issue_labels
- Declared location files:
$issue_location_files

Additional context:
- Previous worker analysis comments, if any:
$pending_comments
- Human comment, if any:
$human_comment

If `$pending_comments` is non-empty, incorporate useful prior analysis.
If `$human_comment` is provided, follow it unless it conflicts with the issue requirements.
If `$test_failure_context` is set, analyze the failure details below, fix the underlying issue, and update any affected files or commands as needed:
$test_failure_context

Scope guard:
$issue_scope_guard

Return a single JSON block in ```json ... ``` with this exact schema:
```json
{
  "files": [
    {"path": "relative/path/to/file.py", "content": "...complete file content..."}
  ],
  "delete_files": ["relative/path/to/obsolete-file.py"],
  "test_command": "pytest tests/ -v",
  "install_command": "pip install -e .",
  "branch_name": "ai/issue-{number}-{short-slug}",
  "commit_message": "feat: implement X for issue #{number}"
}
```

Requirements:
- Treat the project-level context pack as authoritative for product intent, delivery shape, and preserved requirements. The issue body is a local slice, not the whole product contract.
- Include ALL files that need to change, whether new or modified.
- If a file must be removed to satisfy the issue or keep the repo valid, list it in `delete_files`.
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
- **CRITICAL — Do NOT regress existing dependencies**: If the repo already has a `package.json`, `pyproject.toml`, `requirements.txt`, or similar manifest, your new version MUST keep all existing dependencies and scripts unless the issue explicitly removes them.
- **CRITICAL — Respect the current repo state**: If the issue asks you to create a file but that file already exists in the checked-out repo, preserve its existing exports, public APIs, and configuration unless the issue explicitly changes them.
- **Single test config per tool**: Do NOT create duplicate test configs for the same toolchain (for example both `jest.config.js` and `jest.config.cjs`) unless the repo already uses both and the issue requires it.
- **Prefer the repo's existing stack**: If the repository already uses a framework, package manager, database, or test runner, extend that setup instead of introducing a parallel stack.
- **Acceptance-test snippets are behavioral examples**: If the issue body shows raw assertions or example code, preserve the repository's existing test runner and express the same behavior in that runner's normal style unless the repo itself already uses the snippet's style.
- **Do not switch test harnesses accidentally**: If `test_command` runs Jest/Vitest/node:test, write tests that that configured runner will actually execute. Do not convert a working Jest/Vitest test file into bare top-level assertions unless the configured runner supports that style directly.
- **Do not invent extra infrastructure**: Only add databases, ORMs, background workers, or deployment tooling when the issue or the current repository clearly requires them.
- **Respect issue boundaries**: Stay inside the files named in the issue body unless the acceptance test clearly requires a directly related companion file.
- **Test-labeled issues are test-scoped**: If the issue labels include `test`, treat production runtime code as read-only. Only modify the declared test file(s) plus test-support/config files required to run those tests.

Output ONLY the JSON block. No explanation before or after.
