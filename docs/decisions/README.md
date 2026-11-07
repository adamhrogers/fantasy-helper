# Decision log

One short entry per architecture choice: what was decided, why, and what was rejected (spec: Observability and code quality). Entries are never deleted; a reversed decision gets a new ADR that supersedes the old one.

**When to write one:** for choices that are expensive to reverse, or that someone will later ask "why?" about. Smaller decisions live in the doc they affect: a runbook, the architecture, the roadmap. Run-level retries, for example, are in `docs/runbooks/run-events.md`, not an ADR.

**How an ADR changes after it's accepted** (the common practice, which this log follows):
- **The decision is reversed:** write a new ADR, and change the old one's status line to "Superseded by NNNN". Nothing else in the old one changes.
- **The decision holds but grows or gets clearer:** add a dated **Update** section at the bottom of the same ADR, as 0003 and 0011 have. The original text stays as written.
- **Typos and broken links:** just fix them.

Git keeps every version of every ADR (`git log -p docs/decisions/0011-*.md`), and this index shows each one's current status.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-python-langgraph-langsmith.md) | Python, with LangGraph and LangChain for agents and LangSmith for tracing | Accepted · 2026-10-15 (decided in the spec) |
| [0002](0002-ports-and-adapters-with-linted-layers.md) | Ports and adapters, with layers enforced by import-linter | Accepted · 2026-10-16 |
| [0003](0003-one-read-only-mcp-server.md) | One read-only MCP server over our own data model | Accepted · 2026-10-15 (decided in the spec) |
| [0004](0004-code-computes-models-argue.md) | Code computes, models argue | Accepted · 2026-10-16 |
| [0005](0005-append-only-evidence.md) | Append-only evidence: versioned inputs, an insert-only registry and a tip-off guard | Accepted · 2026-10-16 |
| [0006](0006-supabase-postgres-from-day-one.md) | Supabase Postgres from day one | Accepted · 2026-10-19 (changed from the spec) |
| [0007](0007-configuration-homes-and-encrypted-credentials.md) | Where configuration lives, encrypted credentials and row-level security | Accepted · 2026-10-19 |
| [0008](0008-fastapi-on-cloud-run.md) | FastAPI on Cloud Run as the phone app's backend | Accepted · 2026-10-19 |
| [0009](0009-pwa-vite-react.md) | Phone app as a PWA built with Vite, React and TypeScript | Accepted · 2026-10-19 |
| [0010](0010-defer-batch-api.md) | Defer the Batch API | Accepted · 2026-10-25 (changed from the spec) |
| [0011](0011-ntfy-for-ops-alerts.md) | ntfy for ops alerts, separate from product push | Accepted · 2026-10-25 |
| [0012](0012-public-repo-with-redacting-recorder.md) | Public repository with a redacting fixture recorder | Accepted · 2026-10-19 |
| [0013](0013-gradable-takes.md) | Gradable takes: direction, horizon and conviction | Accepted · 2026-10-19 |
| [0014](0014-timelines-from-snapshots.md) | Timeline graphs computed from stored snapshots | Accepted · 2026-10-19 |
| [0015](0015-native-structured-outputs.md) | Native structured outputs: always `method="json_schema"` | Accepted · 2026-10-25 (the M0 check) |
| [0016](0016-espn-direct-http.md) | Call ESPN's API directly; use espn-api only as a reference | Accepted · 2026-11-06 (changed from the spec) |
| [0017](0017-raw-zone-and-interim-snapshots.md) | Raw snapshots on the laptop until M3; ingest loads from them | Accepted · 2026-11-06 |
| [0018](0018-identity-by-name.md) | Match players across platforms by name, conservatively | Accepted · 2026-11-06 |
| [0019](0019-espn-trades-from-activity-feed.md) | Every league trade comes from ESPN's activity feed | Accepted · 2026-11-06 (for the trade tracker, P9) |
| [0020](0020-read-models-and-latency.md) | Low-latency reads: computed ahead, few tools, same region | Accepted · 2026-11-06 |
| [0021](0021-storage-budget-and-season-archive.md) | Storage budget: Postgres holds the current season, R2 keeps everything | Accepted · 2026-11-06 |
| [0022](0022-one-box-score-for-every-league.md) | One box score for every league; leagues differ only in their rules | Accepted · 2026-11-06 |
| [0023](0023-cloud-snapshots-on-r2.md) | Cloud snapshots: GitHub Actions on Eastern time, raw store on R2, overlap before cutover | Accepted · 2026-11-06 |
| [0024](0024-dependencies-update-themselves.md) | Dependencies update themselves: Dependabot PRs that merge once CI passes | Accepted · 2026-11-07 |

## Template

```markdown
# ADR NNNN: Title

**Status:** Proposed | Accepted | Superseded by NNNN · YYYY-MM-DD

## Decision
## Why
## Rejected
## Consequences   (optional)
```
