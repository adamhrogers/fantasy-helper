# ADR 0017: Raw snapshots on the laptop until M3; ingest loads from them

**Status:** Accepted · 2026-11-06

## Decision
`fh snapshot` saves the public data that changes daily (projections, injury statuses, the schedule, Sleeper's player list) as untouched, gzipped raw payloads, twice a day (06:30 full set, 17:30 pre-tip set), through launchd on Adam's Mac. The raw store refuses to overwrite a payload. `fh ingest` loads every snapshot not yet loaded, oldest first, into the database; league data is fetched fresh at ingest and stored raw too. In M3 both move to GitHub Actions and the raw store moves to Cloudflare R2.

## Why
A missed day of projections can never be recovered (ADR 0014), and the season had already started. Laptop scheduling was the fastest way to start history on 2026-11-06. Separating capture (snapshot) from loading (ingest) means history can be reloaded or remodeled at any time from the raw files.

## Rejected
- **Waiting for M3's cloud scheduling:** loses every day until then.
- **Fetching public data at ingest time:** duplicates Sleeper's once-a-day player list request and can't backfill history.

## Consequences
Until M3, a Mac that's shut down (not asleep) at the scheduled time misses that run; a sleeping Mac runs it on wake.

**Update, 2026-11-06 (M3 part 1):** the move to the cloud is built, as ADR 0023 describes: `R2RawStore` with the same layout, `fh raw upload` for the laptop's backlog, and `.github/workflows/snapshot.yml` on Eastern time. Launchd keeps running through a two-day overlap and is then removed (docs/runbooks/cloud-snapshots.md).
