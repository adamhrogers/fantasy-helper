# ADR 0012: Public repository with a redacting fixture recorder

**Status:** Accepted · 2026-10-19

## Decision
The repository is public (spec: the portfolio piece). Fixtures are recorded only through `fh fixtures record` (M1), which strips cookies and auth headers and pseudonymizes leaguemates' names and team names before writing. gitleaks scans every commit locally and the full history in CI.

## Why
Recorded API responses contain other people's data and, in headers, login cookies.

## Rejected
- **A private repository:** fits the free Actions minutes, but loses the portfolio value.
- **Hand-edited fixtures:** easy to get wrong once and leak forever.

**Update, 2026-11-07:** Adam weighed going private before the paid launch and chose to stay public for now. Private on the free plan would lose free secret scanning, the `prod` environment's branch rule and required CI checks. The triggers for going private are in docs/roadmap.md ("When the repo goes private"). The redacting recorder stays either way.
