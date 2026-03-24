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

Output ONLY the JSON block. No explanation before or after.
