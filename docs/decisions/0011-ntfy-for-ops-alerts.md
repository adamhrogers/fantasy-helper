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

**Update, 2026-11-06: run events.** The ntfy channel carries every run's events, not only failures: `started`, `step` and `succeeded` are quiet (low priority, switchable with `FH_NOTIFY_PROGRESS`), `warned` is normal and `failed` is loud (`observability/events.py`, docs/runbooks/run-events.md). It stays ops-only: anything the user acts on (report ready, lock reminders) is product push from the phone app.
