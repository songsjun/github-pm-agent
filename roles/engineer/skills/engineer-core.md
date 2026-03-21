# Engineer Review Skills

## Code Review Approach

**Blocking issues** — must be fixed before merge:
- Correctness bugs, data races, security vulnerabilities
- Missing error handling at system boundaries
- Breaking changes without migration path
- Tests that don't cover the stated behavior

**Non-blocking suggestions** — valuable but not required:
- Style improvements beyond the project's linter config
- Alternative approaches that are equivalent in correctness
- Nitpicks on naming or comments

## Signal Reading

When a PR has CI failures, check if they are:
- Flaky tests unrelated to this change → comment with context, don't block
- Failures directly caused by this PR → label `needs-work`, comment with specifics

When a PR lacks tests:
- Check if the change is covered by integration tests elsewhere before flagging
- If test coverage is genuinely missing, comment with what scenarios to cover

## Comment Format

For blocking issues:
```
**Blocking**: [what is wrong] — [why it matters] — [what to do instead]
```

For suggestions:
```
**Suggestion**: [what could be improved] — [optional: why]
```
