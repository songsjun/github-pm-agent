# Security Review Skills

## Veto Criteria

Only recommend `should_block: true` when the PR introduces:
1. A directly exploitable vulnerability in the changed code (not pre-existing)
2. Hardcoded secrets or credentials committed to the repository
3. Removal of an existing security control without replacement

Do NOT block for:
- Theoretical vulnerabilities that require unrealistic attacker preconditions
- Issues that exist in the codebase before this PR (create a separate finding instead)
- Style or best-practice deviations that are not security-relevant

## Issue Severity

| Severity | Description | Action |
|----------|-------------|--------|
| Critical | Direct exploitation possible, no auth required | Block + label `security:critical` |
| High | Exploitation requires some attacker access | Block + label `security:high` |
| Medium | Limited scope, hard to exploit | Comment + label `security:medium` |
| Informational | Best-practice gap, no direct exploitability | Comment only |

## Sensitive File Patterns

Pay extra attention to changes in:
- `auth/`, `security/`, `**/credentials*`, `**/.env*`
- Dependency manifests (`requirements.txt`, `package.json`, `Gemfile`)
- CI/CD configuration (`.github/workflows/`, `Dockerfile`, `docker-compose.yml`)
- Configuration files with connection strings or tokens
