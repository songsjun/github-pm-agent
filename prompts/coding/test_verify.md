You are interpreting test results for PR #$pr_number for GitHub issue #$issue_number: $issue_title.

Test passed flag: $test_passed

Pytest stdout/stderr:
```text
$test_results
```

Write a concise PR comment that:
- Starts with `## Test Results`
- Includes a ✅ or ❌ status header
- Summarizes what passed or failed
- For failures, includes the exact failing test names and error messages
- Ends with one of these conclusions:
  - `All tests pass — ready for review`
  - `N tests failing — needs fixes`

If `$test_passed` is `true`, report the passing result concisely.
If `$test_passed` is `false`, count the failing tests from the output and use that count in the conclusion.

Output only the markdown comment.
