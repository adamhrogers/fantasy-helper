# ADR 0026: Reports are saved, and built in the cloud every morning

**Status:** Accepted · 2026-11-15 · builds on ADR 0025 (the same scheduler and jobs). **Superseded in part by [ADR 0028](0028-the-morning-is-a-chain.md):** `daily` and `weekly` aren't on their own clocks, they start when the job before them finishes, and a complete snapshot (not just box scores) is what they wait for.

## Decision
- **A `reports` table** (migration 0006) holds each report the scheduled runs build: one row per league, kind (`daily` or `weekly`) and date, with the `ReportDocument`'s cards as JSON. A repeat run **replaces** that day's row but keeps its first id, so feedback that refers to a report (M5) survives a rerun. Deleting a league or user deletes their reports: a beta friend's data has to stay deletable, and a report is rebuildable from the registry anyway. The table has row-level security on, like every table.
- **`daily` and `weekly` are Cloud Run jobs**, every morning, one after the other: the 06:17 snapshot starts `daily`, which starts `weekly` (ADR 0028; this decision first put them on their own Cloud Scheduler entries at 06:45 and 07:00). Same pattern as ADR 0025: one job per task, a fixed command, no retries at the job level (the run retries inside itself), watched by Healthchecks. `scripts/jobs.conf` is the one table of jobs, times and grace periods.
- **Every morning, not only Mondays,** for the weekly overview: its facts (injuries, rosters, who plays when) change daily, and the app's "This Week" tab should show this morning's. A report is a few KB per league, so a row a day is nothing. Mondays only is one line in `jobs.conf`.
- **The scheduled runs insist on a complete morning snapshot from today** (`--require-snapshot-today`, set in `deploy.yml`). This first checked only that box scores existed; ADR 0028 replaced that with a completion marker the snapshot writes only when every dataset saved and passed. If the marker isn't there, the run waits and retries (after 2 and 5 minutes, the existing retry), then fails loudly: "no complete morning snapshot yet". Runs started by hand don't insist. Without this a missing or partial snapshot would quietly build reports and log calls from yesterday's inputs.
- **Runs that write take turns.** Each takes one Postgres advisory lock (`pg_advisory_xact_lock`) before it touches rows, so a double start (Cloud Scheduler is at-least-once, and one test start did run twice) or a slow daily run overlapping the weekly one can't ingest the same rows at once. The second waits, then finds nothing new to do. A wait over 10 minutes fails that attempt, which retries.
- **Healthchecks watches `daily` and `weekly`.** `daily`'s check alerts at 07:45 if the report isn't done, the late-report alert the run-events runbook promised (ADR 0028 explains the cron time and grace). **A run that fails exits non-zero and pings `fail`**: a league failing, or the run not finishing. **Warnings don't** (ADR 0028): this decision first made every degraded run fail, which would have raised a "down" alert every morning over a long-lasting warning. `fh ingest` started by hand is not watched: its first ping would create a check with Healthchecks' default one-day schedule, which would alert about a run nobody schedules.
- **`fh run daily`, `fh run weekly` and `fh ingest` share their error handling** (`cli/_runs.py`, `cli/_common.py`): retries, one message for each way a run stops, `crash_guard` (an unexpected error still alerts the phone), and the exit code `REPORTED_EXIT`. This closes the item tabled from the PR #4 review.
- **The Healthchecks schedules are set by `scripts/healthchecks-setup.sh`**, from `jobs.conf`, through Healthchecks' API, and always in `America/New_York`. On 2026-11-14 they were typed into the website and saved in UTC (its default), which raised a false "down" alert for the evening snapshot.
- **The prod registry starts empty at go-live** (decided 2026-11-06). Snapshot history is loaded into prod once, from R2, by running `ingest` as a Cloud Run job.
- **`push_subscriptions` moves to part 3**, with the API route that defines its shape. A table nothing writes to yet would only be a guess.

## Why
M3's exit test is a report that reaches Adam's phone with the laptop closed, and ADR 0025 made timed jobs on time. The reports have to be somewhere the API can read them with one indexed query (ADR 0020: hot reads come from pre-computed rows), and the app renders cards, not HTML, so the `ReportDocument` (the contract the phone renders) is what's stored.

The freshness gate exists because a quiet fallback is the worst failure for a product built on never being wrong: a report from yesterday's box scores looks exactly like a good one. Failing loudly costs one alert; the 07:45 check turns a silent miss into a message.

The lock is cheap and removes a class of races that tests don't find. A transaction-level lock releases itself when the run's transaction ends, even if the process is killed, so a crashed run can't leave the database locked.

## Rejected
- **Storing reports as rendered HTML, or as files in R2:** the app renders cards from the contract, and the API needs per-user access control and a latest-first query, which a table gives for free.
- **A new row per run instead of replacing:** two runs on a day would show two reports; "latest" would need tie-breaking; a rerun after a code fix couldn't correct a bad card.
- **Running the reports on GitHub Actions:** ADR 0025's reason, again. A morning report on that scheduler arrives in the afternoon.
- **Skipping the freshness gate and relying on the snapshot's own alert:** the snapshot's alert says the snapshot failed, not that the report was built from old data. They're different facts.
- **A session-level advisory lock, or a lock table:** a session lock outlives a crashed transaction on some poolers; a table needs cleanup. The transaction lock needs neither.
- **Putting the weekly overview on Mondays only:** see Decision.
- **Pinging Healthchecks from `fh ingest`:** see Decision.

## Consequences
- **A run with a failed league exits 3**, where `fh run daily` used to exit 0 with an alert on the phone. By hand that's visible as a non-zero exit; in the cloud it's a `fail` ping and an execution marked failed. A run with only warnings still exits 0.
- **The first history load is the heaviest run.** About 1,900 raw files, roughly 140,000 projection rows. The report jobs get 1 GiB and a 30-minute limit for it, and the runbook says how to raise the limit for a one-off if it's not enough.
- **Database size:** that history is about 25 MB of the 500 MB free plan (estimate; ingest reports the real number, and warns at 60%).
- **Two Cloud Scheduler entries** (the two snapshots) after ADR 0028, inside the free tier's three. This decision first made it four.
- **Healthchecks has four checks to keep right** (`migrate` has none: the deploy waits for it and fails visibly). Adding a job means a line in `jobs.conf`, a `deploy` line in `deploy.yml`, and a run of `scripts/gcp-setup.sh` and `scripts/healthchecks-setup.sh`.
