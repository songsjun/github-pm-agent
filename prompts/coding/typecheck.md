You are producing a human-readable typecheck summary for PR #$pr_number (issue #$issue_number: $issue_title).

Typecheck passed: $typecheck_passed
Typecheck output:
$typecheck_output

---

Output a short markdown summary (3–5 lines) for the gate comment. Cover:
- Whether the TypeScript compiler reported errors
- The exact error count if failed (extract from output above)
- Whether this blocks the merge (yes if typecheck_passed is false)

If `$typecheck_passed` is `true` and the output is clean, output only:
`TypeScript compilation: PASS — no type errors.`

Do not add opinions or suggestions. Report only what the output shows.
