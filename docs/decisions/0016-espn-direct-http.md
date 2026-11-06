# ADR 0016: Call ESPN's API directly; use espn-api only as a reference

**Status:** Accepted · 2026-11-06 (changed from the spec)

## Decision
The ESPN adapter calls ESPN's (unofficial) endpoints directly with httpx: the same URLs, views and `x-fantasy-filter` headers the community `espn-api` library uses. That library's code tables (stat IDs, lineup slot IDs, team IDs) are copied as small, attributed tables and checked against real responses in the M1 spike. `espn-api` is not a dependency.

## Why
Daily snapshots and recorded fixtures need the **exact raw responses**, and `espn-api` parses them into objects and discards the rest. Owning the HTTP layer also gives one place for retries and for keeping cookies out of every log and file (the `secret_headers` channel), and it removed a dependency on a library that could change underneath us.

## Rejected
- **Wrapping `espn-api` objects (the spec's plan):** hides the raw responses the evidence system depends on.

## Consequences
When ESPN changes something, the fix lives in `adapters/espn/` and the contract tests show exactly what changed. `espn-api`'s source stays the first place to look for new endpoints.
