You are fixing code in pull request #$pr_number for GitHub issue #$issue_number: $issue_title.

## Issues found in code review (round $review_round)

$artifact_code_review_combined

## Current PR diff

```diff
$pr_diff
```

## Original implementation plan

$artifact_implement

## Test failure context (if tests failed after a previous fix)

$test_failure_context

**Approved technical design** — use this to verify that fixes stay consistent with the project architecture.
$repo_tech_design

**Project conventions** — framework patterns, error handling, auth, test setup. Fixes must comply with these.
$repo_conventions

**Current content of the target file** — if the file exists in the repo, its full content is shown below. Preserve ALL existing exports and only ADD or fix what the review requires.
$existing_file_contents

**Current content of dependency files** — these are the actual files this issue's file imports from. Use only the exports shown here when fixing import errors or type mismatches.
$dependency_interfaces

---

Fix ONLY the blocking issues identified in the code review above. Do not change unrelated code.

If the project is missing setup files required to run tests (package.json, tsconfig.json, pyproject.toml, etc.), include them. Derive the correct install sequence from `$repo_tech_design`.

Use the SAME `branch_name` as the original implementation: extract it from the JSON in `$artifact_implement`.

Return a single JSON block in ```json ... ``` with this exact schema:

```json
{
  "files": [
    {"path": "relative/path/to/file", "content": "...complete file content..."}
  ],
  "test_command": "...",
  "install_command": "...",
  "branch_name": "ai/issue-$issue_number-...",
  "commit_message": "fix: address code review findings (round $review_round) for issue #$issue_number"
}
```

Requirements:
- Provide FULL file content for every changed file, not diffs.
- The fix must make all tests pass.
- Do NOT create a new branch — use the exact branch name from the original plan.
- Derive `install_command` from `$repo_tech_design` — use the same install sequence as the original implementation.
- If `$existing_file_contents` shows a non-empty file, preserve ALL existing exports.
- If `$dependency_interfaces` is provided, use only the exports shown there when fixing import issues.

Output ONLY the JSON block. No explanation before or after.
