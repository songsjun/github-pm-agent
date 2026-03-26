You are resolving a merge conflict in pull request #$pr_number for GitHub issue #$issue_number: $issue_title.

## Workflow feedback

$human_comment

## Deterministic merge-conflict probe

$merge_conflict_details

## Current PR diff

```diff
$pr_diff
```

## Original implementation plan

$artifact_implement

## Latest test result

$artifact_test_result

---

Update the existing PR branch so it merges cleanly with the latest base branch while preserving the intended feature behavior. If the branch is out of date with the base branch, incorporate the required base-branch changes into the conflicting files and resolve the conflict deterministically.

Requirements:
- Prefer the smallest possible change set. Only edit files that are actually needed to resolve the merge conflict or keep tests passing.
- Keep the original branch name from `$artifact_implement`.
- Preserve existing issue scope. Do not add unrelated changes.
- Preserve the existing project setup. Do not rewrite `package.json`, `tsconfig.json`, `jest.config.*`, lockfiles, or other build/test configuration files unless they are themselves part of the conflict or the current branch is already broken because of them.
- If the conflicted repo state contains mutually exclusive files and one must be removed to restore a valid build or test setup, list that path in `delete_files`.
- If previous review warnings/blockers exist, do not regress them.
- Return FULL file contents for every changed file.
- The result must make the PR merge cleanly and keep tests passing.

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
  "commit_message": "fix: resolve merge conflict for issue #$issue_number"
}
```

Output ONLY the JSON block. No explanation before or after.
