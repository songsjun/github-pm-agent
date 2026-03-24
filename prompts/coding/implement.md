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

Output ONLY the JSON block. No explanation before or after.
