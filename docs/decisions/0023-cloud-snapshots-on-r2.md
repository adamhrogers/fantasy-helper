# ADR 0023: Cloud snapshots: GitHub Actions on Eastern time, raw store on R2, overlap before cutover

**Status:** Accepted · 2026-11-06

## Decision
- **The raw store moves to Cloudflare R2** (bucket `fantasy-helper-raw`), through its S3-compatible API with `boto3`. It uses the same keys, bytes and sidecars as the local store, so a local backlog uploads byte for byte (`fh raw upload`) and either store can be read the same way. `FH_RAW_STORE` picks `local` or `r2`. One bucket serves every environment: raw payloads are facts about the outside world, not environment data, and ingest in dev or prod reads the same history.
- **Never overwritten, by two guards.** Every write first checks that the object doesn't exist, then sends `If-None-Match: *`, which R2 turns into an atomic refusal. `fh doctor --only raw` proves the second guard against the live bucket each time it runs. Writes carry `Content-MD5`, so R2 rejects a corrupted body. Each sidecar is written before its payload, so a reader that lists payloads can always read their sidecars.
- **Snapshots run on GitHub Actions** (`.github/workflows/snapshot.yml`) at 06:17 and 17:17 Eastern, in the `prod` environment. GitHub's cron is UTC, so each set has one entry for EST and one for EDT. `fh snapshot --cron` runs only the entry that matches New York's offset that day (`pipeline/cron.py`, tested across both changes and kept in step with the workflow by a test). Minutes are off the hour because GitHub delays scheduled runs most at the top of the hour.
- **An R2 outage is retryable** (`FailureKind.STORAGE_DOWN`): nothing was saved, so trying again can help. A misconfigured or refused write is not.
- **Cutover with an overlap.** The laptop's backlog goes up first. Then the laptop (launchd) and the cloud both write to R2 for at least two clean days before launchd is removed. Keys carry microseconds, so the two never collide. Sleeper's player list stays at one request a day: a once-a-day dataset that any run already saved today isn't fetched again.
- **Failures still reach the phone.** The app reports its own failures, and now also crashes (`<set> snapshot crashed`, naming only the exception type). A workflow step alerts through ntfy when the job breaks before Python runs. A run that never starts is the job of the Healthchecks.io watchdog (M3 part 5).

## Why
Rule 8: a missed snapshot day can't be recovered, and a laptop that's shut down skips its jobs. GitHub Actions runs on schedule regardless, is free for a public repo, and already runs CI. Its runners keep no files between runs, so the raw store has to be hosted, and R2 was already chosen for raw, archives and backups (ADR 0021).

The overlap means no day depends on an untested path: ESPN and Sleeper have never been called from GitHub's servers, and the cloud schedule has never fired. The laptop keeps saving until the cloud has proven itself.

## Rejected
- **One cron entry per set, in UTC:** the sets would move an hour on 2027-03-14, inside the season, and the evening set would land an hour closer to tip-off.
- **Gating on the local hour alone:** a late start near the hour boundary could run a set twice or not at all. The entry that fired, compared with the offset, is deterministic.
- **A hand-written S3 client over httpx:** signing requests is security-sensitive code to own. `boto3` is the reference client, and its conditional writes and checksums are configurable.
- **Relying only on `If-None-Match`:** if R2 ever ignored it, an overwrite would go unnoticed. The check before each write costs one request.
- **Separate buckets per environment:** a second copy of identical public data, and prod's history would start empty.
- **Cutting over in one step:** the first cloud failure would be a lost day.

## Consequences
- The laptop's `.env` gets the R2 values (`FH_RAW_STORE=r2`) during the overlap, so both machines write to the bucket. The local `data/raw` stays as a backup copy.
- Ingest reads each payload's time from its key instead of its sidecar, saving one R2 request per payload on every run.
- `prod` is limited to `main`, and GitHub runs scheduled workflows only from the default branch, so this milestone merges in parts rather than as one PR at the end.
- The snapshot job doesn't log runs to the database until prod is migrated (M3 part 2). A snapshot without `DATABASE_URL` succeeds and says the run wasn't logged.
