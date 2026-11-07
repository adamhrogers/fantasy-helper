# Moving snapshots to the cloud (M3 part 1)

**Written:** 2026-11-06. **Revised:** 2026-11-07: the laptop keeps saving to its own disk, and keeps its schedule until the missed-run watchdog is live (ADR 0023, Update). **Who:** Adam, with Claude checking each step. **Design:** ADR 0023.

The snapshot job moves from the Mac's schedule (launchd) to GitHub Actions, and the raw files move from `data/raw` to the R2 bucket. No day may be missed (rule 8), so the Mac keeps saving on its own until the cloud has proven itself and something watches for runs that never start.

## Progress

- [x] Setup sections 1, 3 and 4 are done ([m3-account-setup.md](m3-account-setup.md)). The `prod` secrets were checked on 2026-11-07.
- [x] PR #4 merged on 2026-11-07 at 10:56 Eastern. **The cloud schedule has been live since then**, at 06:17 and 17:17 Eastern.
- [x] The laptop runs the M3 code (main checkout pulled to `02c3f0f` on 2026-11-07).
- [x] **1.** The laptop has the R2 values, and `fh doctor --only raw` passed against the live bucket (2026-11-07).
- [ ] **2.** Upload the backlog. 1,716 files were waiting at the dry run (2026-11-07, 12:30 Eastern).
- [ ] **3.** The first cloud run: scheduled for 2026-11-07 at 17:17 Eastern.
- [ ] **4.** The overlap: four clean cloud runs in a row, **and** the Healthchecks pings live (part 5).
- [ ] **5.** End of the overlap, with Claude's go-ahead: remove launchd, give the laptop its own R2 key, and switch it to R2.

All commands run in your own Terminal app, from `~/projects/fantasy-helper`.

## 1. Give the laptop the R2 values (done 2026-11-07)

`.env` has `FH_R2_ENDPOINT`, `FH_R2_ACCESS_KEY_ID` and `FH_R2_SECRET_ACCESS_KEY`, and no `FH_RAW_STORE` line, so the laptop still writes to its own disk. The endpoint is `https://<account id>.r2.cloudflarestorage.com`, with **nothing after `.com`**. Cloudflare's bucket page shows it with `/fantasy-helper-raw` on the end; with that ending, writes would land in the wrong place. To check again:

```bash
uv run fh doctor --only raw
```

Expect `✓ raw  R2 bucket 'fantasy-helper-raw' configured (FH_RAW_STORE=local); it refuses to overwrite objects`. Anything else: stop and tell Claude.

## 2. Upload the backlog (2 min)

The backlog is every file the laptop has saved: public snapshots, and the league responses that `fh ingest` keeps (about half the files). See what would go up first:

```bash
uv run fh raw upload --dry-run
```

Upload them. Each file is checked against R2's listing afterwards:

```bash
uv run fh raw upload
```

Run it once more. It should say every file is already in R2, and upload nothing:

```bash
uv run fh raw upload
```

## 3. The first cloud run

Nothing to do: the schedule starts it at 17:17 Eastern. To see it work sooner, use **Actions → Snapshot → Run workflow** and choose **evening**; that's safe at any hour.

- It should finish green in a minute or two, and your phone gets a quiet `Evening snapshot succeeded` on the **prod** topic. The laptop's own success follows at 17:30 on the dev topic.
- If it fails, the phone says why, on the prod topic. Tell Claude. The laptop's 17:30 run saves the set either way.
- A wrong endpoint can still accept the files, in the wrong place. Bad keys can't save anything. Both end in `Evening snapshot: raw store check failed`.

## 4. The overlap: two copies that can't fail together

Both schedules run, and each saves to its own place: the cloud to R2 (06:17 and 17:17), the laptop to `data/raw` (06:30 and 17:30). **Don't add `FH_RAW_STORE=r2` to `.env` yet.**

**Why separate places:** the overlap is only a backup if the two runs can't fail together. If both saved to R2 with the same key and the same new code, one bad key, one bug or one R2 outage would stop both in the same evening. The laptop's disk is the path that has saved every day since 2026-11-06.

**What you'll see:** two quiet successes per set, one on each topic. The laptop's morning run fetches Sleeper's player list for its own copy, so the morning message `already saved today by another run` doesn't appear.

**How long:** until both of these are true. Then tell Claude.
- **Four clean cloud runs in a row** (two mornings, two evenings). A failed cloud run restarts the count once it's fixed.
- **The Healthchecks watchdog is live** (M3 part 5, setup section 6). GitHub can delay scheduled runs, and when it's busy it drops some. A run that never starts sends no alert, so until something watches for missing runs, the laptop is the only cover.

Leave the Mac on or asleep at 06:30 and 17:30, not shut down. A sleeping Mac runs the job when it wakes; a Mac that's shut down skips it.

**If a cloud set failed or never ran,** the laptop's copy fills the gap:

```bash
uv run fh raw upload
```

It uploads every file R2 doesn't have yet. That includes the laptop's copies of sets the cloud also saved, which is harmless: nothing is overwritten, box scores and injury changes load once, and projections just gain a second capture a few minutes apart.

**To pause the cloud job** (if it keeps failing): **Actions → Snapshot → ··· → Disable workflow**. The laptop keeps saving.

## 5. End of the overlap (with Claude's go-ahead)

1. Remove the Mac's schedule, then check that both jobs say `not installed`:

```bash
scripts/snapshot-schedule.sh uninstall
```

```bash
scripts/snapshot-schedule.sh status
```

2. Upload once more, which sends the laptop's copies of the overlap days (harmless, as above):

```bash
uv run fh raw upload
```

3. **Give the laptop its own R2 key**, so either machine's key can be revoked without breaking the other. In Cloudflare, go to **R2 → Manage API tokens → Create API token**, choose **Object Read & Write**, scope it to `fantasy-helper-raw`, and save the values in Bitwarden. Put its access key ID and secret into `.env` in place of the current ones. GitHub keeps the current token. The laptop's key has to be able to write, because `fh ingest` saves each league response it fetches.
4. Add `FH_RAW_STORE=r2` to `.env`, so the ingest and report runs you start by hand use the same history as the cloud:

```bash
uv run fh doctor --only raw
```

Expect `✓ raw  R2 bucket 'fantasy-helper-raw' configured (FH_RAW_STORE=r2); it refuses to overwrite objects`.

5. `data/raw` stays as an old backup and stops growing.

## Later: protecting the history from deletion (part 5)

An R2 bucket lock stops anyone deleting or overwriting objects, even with a leaked key: our keys can't change bucket settings. It has to cover only the **public** datasets (`espn/players_projections/`, `sleeper/projections_season/` and the rest), not `espn/leagues/` or `sleeper/leagues/`: a beta friend must be able to have their league data deleted. Locks are set per prefix, one rule per dataset, so part 5 sets them alongside the backups.

**Rolling back:** before step 5 there's nothing to undo; the laptop never stopped saving to its own disk. After step 5, delete the `FH_RAW_STORE=r2` line and reinstall the schedule (`scripts/snapshot-schedule.sh install`).
