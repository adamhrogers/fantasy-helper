# ADR 0011: ntfy for ops alerts, separate from product push

**Status:** Accepted · 2026-10-25

## Decision
Operator alerts (failed run, stale source, expired ESPN cookies, late report) go to a private, randomly named ntfy topic (`fh keygen --topic`) through ntfy's JSON publish API. Without a topic configured, they go to the log.

## Why
The product's own push can be the thing that's broken, and a broken app can't report its own outage. ntfy is free, needs only an HTTP POST, and has a phone app.

## Rejected
- **Pushover:** a one-time cost for the same job.
- **Email from GitHub Actions:** covers job failures only, not stale data or late reports.

## Consequences
Anyone who knows the topic name can read it, so alerts never contain secrets or league data.
