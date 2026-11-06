# ADR 0021: Storage budget: Postgres holds the current season, R2 keeps everything

**Status:** Accepted · 2026-11-06

## Decision
- **Postgres (Supabase Free: 500 MB database size, 2 GB fixed disk, no automatic backups) holds the current season, compactly.**
  - Projection values are stored as **one `REAL` column per stat**, not a JSONB blob.
  - Indexes match the read paths.
  - A daily size check raises an ops alert at 60% (300 MB) and 80% (400 MB).
- **Cloudflare R2 keeps everything, forever:**
  - every raw payload, as now (rule 8)
  - an **end-of-season archive**: each season's tables exported to Parquet, one folder per season (`archive/season=2027/…`), readable with DuckDB without any database
  - a **nightly backup** of the tables that can't be rebuilt from raw: users, leagues, credential ciphertext, takes, recommendations, grades, feedback, FactSheets and reports
- **Season rollover**, after each season's finals:
  1. Export the season to Parquet and verify the row counts.
  2. Delete the season's bulky rows (projections, daily rosters) from Postgres.
  3. Keep the small, high-value tables (players and the crosswalk, game logs, registry, takes, grades) in Postgres.
- **Every season-scoped table carries a season**, directly or through its league, so a season can be exported and removed cleanly.

## Why
Measured on 2026-11-06 with a synthetic load at the real snapshot cadence (about 4,500 projection rows a day) on a local Postgres 17:
- **Current schema, one season:** 720,000 projection rows at about 380 bytes each at real stat counts, so about 275 MB by mid-April. With rosters, FactSheets, reports and Supabase's own schemas, the database ends the season around 350–400 MB, 70–80% of the quota.
- **Current schema, five seasons:** 2 GB. That's past the free disk, which turns read-only at 95% and shuts down when full.
- **Typed columns:** 173 bytes a row, so projections take about 125 MB a season and the whole database ends the season around 200–250 MB, even with about 10 beta leagues.
- **The archive is tiny:** one season of projections as Parquet (zstd) is about 13 MB, against about 275 MB in Postgres. R2's 10 GB free tier holds decades of seasons, plus the raw payloads at about 0.3 GB a season.

The free plan has no backups, so the only irreplaceable data (the registry, takes and grades: the season's evidence) needs its own copy. Everything else can be rebuilt from raw with `fh ingest`.

## Rejected
- **Upgrading to Supabase Pro ($25 a month, 8 GB):** not needed; this keeps us inside the free tier. It stays the fallback for the paid launch.
- **Storing only changed projection rows now:** it would cut size further, but every "projections as of pull X" read would need an as-of query and tombstones for dropped players, and it changes the plain-snapshot model of ADR 0005. **It's the first lever if the 60% alert fires**, alongside storing roster changes instead of a full roster every day.
- **Keeping every season in Postgres:** it doesn't fit the free disk, and history is read in batch, where Parquet is better anyway.

## Consequences
- **M2:** migration 0003 converts `projection_rows` to typed columns (8,490 rows today, trivial to convert or reload from raw) and adds the size check.
- **M3:** adds the nightly backup and the size alert to the scheduled jobs.
- **P11:** builds the season rollover.

Watch two gotchas when scheduling: Supabase Free pauses a project after a week with no activity, and GitHub disables scheduled workflows in a public repo after 60 days without a commit. Both matter in the offseason.
