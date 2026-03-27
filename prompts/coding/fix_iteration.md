You are fixing code in pull request #$pr_number for GitHub issue #$issue_number: $issue_title.

## Issues found in code review (round $review_round)

$artifact_code_review_combined

## Current PR diff

```diff
$pr_diff
```

## Original implementation plan

$artifact_implement

## Project-level context pack

$project_context_pack

## Test failure context (if tests failed after a previous fix)

$test_failure_context

## Additional workflow feedback

$human_comment

## Issue labels

$issue_labels

## Declared location files

$issue_location_files

## Scope guard

$issue_scope_guard

---

Fix ONLY the blocking issues identified in the code review above. Do not change unrelated code.

If the project is missing setup files required to run tests (package.json, tsconfig.json, pyproject.toml, etc.), include them.

Use the SAME `branch_name` as the original implementation: extract it from the JSON in `$artifact_implement`.

Return a single JSON block in ```json ... ``` with this exact schema:

```json
{
  "files": [
    {"path": "relative/path/to/file", "content": "...complete file content..."}
  ],
  "delete_files": ["relative/path/to/obsolete-file"],
  "test_command": "...",
  "install_command": "...",
  "branch_name": "ai/issue-$issue_number-...",
  "commit_message": "fix: address code review findings (round $review_round) for issue #$issue_number"
}
```

Requirements:
- Preserve the product intent and delivery contract from the project-level context pack. Do not "fix" the current issue in a way that narrows the promised product.
- Provide FULL file content for every changed file, not diffs.
- If a file must be removed to make the tests pass or to resolve the review finding, list it in `delete_files`.
- The fix must make all tests pass.
- Do NOT create a new branch — use the exact branch name from the original plan.
- Preserve the repository's existing test runner and test harness. Treat acceptance-test snippets in the issue body as behavioral examples, not literal instructions to switch a Jest/Vitest/node:test file into a different execution style.
- If the configured `test_command` runs Jest/Vitest, keep the test file in a format that runner will execute. Do not rewrite tests into bare top-level assertions unless the configured runner supports them directly.
- Respect the original issue boundary. Stay inside the declared issue file(s) unless a directly related test-support/config file must also change.
- If the issue labels include `test`, do not modify production runtime modules. Limit changes to the declared test file(s) and test-support/config files.

Output ONLY the JSON block. No explanation before or after.
