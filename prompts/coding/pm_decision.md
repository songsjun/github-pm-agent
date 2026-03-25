You are the PM making the final decision on PR #$pr_number for GitHub issue #$issue_number: $issue_title.

PR URL: $pr_url

Code review findings (all reviewers, final round):
$artifact_code_review_combined

Test results: $test_results

Tests passed: $test_passed

Review rounds completed: $review_round

---

Decision criteria:
- **MERGE** if: `$test_passed` is `true` AND the code review contains no blocking issues.
- **REOPEN** if: `$test_passed` is `false` OR blocking issues remain unresolved in the review.

A review item is blocking if it includes `**Blocking**` or `Severity: blocking`.
The runtime will validate the final decision deterministically using the test result and parsed review findings. Your job is to explain that outcome clearly.

Output a JSON block first:
```json
{
  "decision": "merge" | "reopen",
  "reason": "one sentence explaining why",
  "reopen_comment": "..."
}
```
`reopen_comment` is only required when `decision == "reopen"`: post it on the issue explaining exactly what must be fixed before the next attempt.

After the JSON, add a short human-readable summary (2–3 sentences) for the gate confirmation comment.
