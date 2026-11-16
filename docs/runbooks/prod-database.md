# The prod database and the report jobs (M3 part 2)

**Written:** 2026-11-15. **Design:** ADR 0026 (the reports), ADR 0027 (running prod), ADR 0028 (the morning chain), ADR 0025 (the jobs and the scheduler). **Who:** Claude runs the cloud steps and checks each one. Adam adds two secret values, types the ESPN cookies once, reads the first report, and sets the Healthchecks schedules with one script.

All commands run in your own Terminal app, from `~/projects/fantasy-helper`, unless a step says Claude does it.

## What runs where

| Job | Started by | Command | Account | Reads |
|---|---|---|---|---|
| `snapshot-morning` | Cloud Scheduler, 06:17 Eastern | `fh snapshot --set morning` | `fh-jobs` | R2, ntfy, Healthchecks |
| `snapshot-evening` | Cloud Scheduler, 17:17 | `fh snapshot --set evening` | `fh-jobs` | the same |
| `daily` | **`snapshot-morning`, when everything saved and passed** | `fh run daily --require-snapshot-today` | `fh-reports` | the same, plus the database and the encryption key |
| `weekly` | **`daily`, when it finishes** | `fh run weekly --require-snapshot-today` | `fh-reports` | the same |
| `migrate` | the deploy | `fh migrate` | `fh-reports` | the database |

**The morning is a chain, not three clocks** (ADR 0028): `snapshot-morning` → `daily` → `weekly`. Only the first is on a schedule. The snapshot writes a completion marker and starts `daily` only if **every** dataset saved and passed its checks (projections, box scores, injuries, the schedule, all of it); if anything failed, nothing downstream runs, and the snapshot's own alert says what. `daily` starts `weekly` when it finishes, whatever its result (unless it never got going). Each job may start only the next one.

`daily` ingests (fresh league data plus every new snapshot), decides lock or pass for every open starter in the lock-in league, logs each call in the registry, and saves a daily report. `weekly` does the same for the weekly overview in all three leagues. Both save their reports in the `reports` table, and the phone app (parts 3 and 4) will read them from there.

If `daily` hasn't reported by **07:45** (`weekly`: 08:00), Healthchecks alerts the prod topic: that is the late-report alert. A run fails (exit 3, a `fail` ping) only when a league failed or the run couldn't finish; warnings such as a Sleeper player not linked yet go to the phone as a warning and don't count.

## First-time rollout

**Where it stands (2026-11-16): every step is done.** The chain first ran by hand on the evening of 2026-11-15, and then **on its own at 06:17 Eastern on 2026-11-16**: the snapshot started at 06:17:05 and saved 9 of 9, `daily` and `weekly` followed, and all three succeeded by 06:20 with no league errors. The order and the traps from rollout day: [m3-plan.md](../m3-plan.md), Phase 0.

- [x] **1. Claude, before merging** (done 2026-11-15; the job policies show `fh-jobs` may start `daily` and `fh-reports` may start `weekly`)**:** from the PR's branch, add the new pieces. It only adds: the `fh-reports` account, two empty secrets, three placeholder jobs (`migrate`, `daily`, `weekly`), and the chain's permissions (`fh-jobs` may start `daily`, `fh-reports` may start `weekly`, nothing broader). The deployer can't create jobs, so they have to exist before the first deploy.

```bash
scripts/gcp-setup.sh infra
```

- [x] **2. Adam: two secret values** (done 2026-11-15; each has one enabled version). Paste each from Bitwarden at the hidden prompt. The database one is Supabase's **session pooler** string (port 5432, not 6543). The encryption key is the prod one that GitHub's `prod` environment already holds:

```bash
scripts/gcp-secrets.sh fh-database-url fh-encryption-key
```

- [x] **3. Merge the PR** (done 2026-11-15 at 09:27 Eastern; the deploy took 95 seconds, the deployer's job-level role was enough to run `migrate` and wait for it, and the job's log says `migrated an empty database to 0006`)**.** The deploy builds the image, updates the snapshot jobs, then **migrates the prod database** (0001 to 0006: its first migration) and waits for it, then updates `daily` and `weekly`. Claude watches it. If it stops at the migration, the report jobs still run their old image and nothing is half-done.
- [x] **4. Check prod's database** (done by Adam, 2026-11-15; the migration is already confirmed by the job's log, so this adds the region. On 2026-11-15 the app's permission check stopped Claude's session from reading prod secrets, so it's your command unless you allow it): the doctor names the Supabase region and says whether the migrations are at head. Expect `migrations at head (0006), Supabase us-east-1`:

```bash
scripts/fh-prod.sh doctor
```

- [x] **5. Adam: add the three leagues to prod** (done 2026-11-15)**:** (10 min). Your dev database already knows their IDs:

```bash
uv run fh league list
```

  Then add each one to prod. The first command also asks for your email, name and time zone (once), and for the ESPN cookies at a hidden prompt (once; they're stored encrypted with the prod key). Where to find the cookies: [adding-leagues.md](adding-leagues.md).

```bash
scripts/fh-prod.sh league add espn LEAGUE_ID
```

```bash
scripts/fh-prod.sh league add espn OTHER_LEAGUE_ID
```

```bash
scripts/fh-prod.sh league add sleeper LEAGUE_ID --username YOUR_SLEEPER_NAME
```

- [x] **6. Claude: load the history from R2 into prod, then run the chain once** (done 2026-11-15, evening: the load took 8.5 minutes, 101 projection snapshots, 90,508 projection rows, three leagues, database 45 MB of 500 MB; then a hand-started `snapshot-morning` completed, started `daily`, which started `weekly`, all three succeeded, so the chain's hand-over and permissions are proven)**.** First one `ingest` run, started as the `daily` job with its command replaced. It reads every snapshot since 2026-11-06 (about 1,900 files) and loads them. Claude checks the counts against dev afterwards:

```bash
gcloud run jobs execute daily --region us-east4 --args=ingest --wait
```

  If the history load hits the 30-minute limit, Claude runs it again with a longer limit for that one run (add `--task-timeout=60m`). Ingest is one transaction: a run that's cut off saves nothing, so the second run starts from the beginning, and it's safe to repeat.

  Then start the **morning snapshot** by hand, not `daily`. `daily` and `weekly` wait for a completion marker from today, and the 06:17 snapshot on rollout day ran the image from before the merge, which wrote none: `daily` started directly would wait 7 minutes and fail with "this morning's snapshot isn't saved yet". The snapshot saves a fresh set (Sleeper's player list is reused), writes the marker, and starts `daily`, which starts `weekly`. It's also the first live test of the hand-over:

```bash
gcloud run jobs execute snapshot-morning --region us-east4 --wait
```

  Check that the chain ran: one succeeded run each.

```bash
gcloud run jobs executions list --job daily --region us-east4 --limit 3
```

```bash
gcloud run jobs executions list --job weekly --region us-east4 --limit 3
```

  If the phone says "couldn't start daily", see the table at the end. A run started after some of the day's games have tipped logs fewer calls than a morning run: a call is only logged while the player's next tip is still ahead.

- [x] **7. Adam: open the report the cloud built** (done 2026-11-16, the weekly one at least)**:**

```bash
scripts/fh-prod.sh report open
```

  And the weekly overview:

```bash
scripts/fh-prod.sh report open weekly
```

- [x] **8. Claude: confirm the schedules** (checked 2026-11-15 by listing them: exactly two entries, `snapshot-morning` at 06:17 and `snapshot-evening` at 17:17, `America/New_York`, both enabled; `daily` and `weekly` have none, so there was nothing to remove)**.** Only the two snapshots are on a clock; this also removes any schedule a chained job has, because one would start it whether or not the snapshot was complete:

```bash
scripts/gcp-setup.sh schedule
```

- [x] **9. Adam: the Healthchecks schedules, by script** (done 2026-11-15; all four checks showed recent, on-time pings on 2026-11-16; 3 min, and the last time you type a schedule into their website; do it on rollout day, because until it runs the two snapshot checks keep the UTC schedules that raised the false alert). In Healthchecks, open your project → **Settings → API Access → Create API key (read-write)**, save it in Bitwarden, then run the script and paste the key at the hidden prompt. It sets all four checks (cron line, time zone `America/New_York`, grace) from `scripts/jobs.conf`, and it fixes the two snapshot checks that were saved in UTC on 2026-11-14:

```bash
scripts/healthchecks-setup.sh
```

- [x] **10. Clean up, after one clean scheduled morning** (done by Adam, 2026-11-16, after the 06:17 chain ran on its own)**:** nothing in GitHub uses the database URL or the encryption key now. The report jobs read their own copies from Secret Manager (checked 2026-11-15: no workflow names either), and the morning of 2026-11-16 proved those copies work, which is why this waited. Until then GitHub's copies were the spare. Removing them leaves each secret in fewer places, so there are fewer places it can leak from; the values are still in Secret Manager and Bitwarden. Remove them from GitHub's `prod` environment:

```bash
gh secret delete DATABASE_URL --env prod
```

```bash
gh secret delete FH_ENCRYPTION_KEY --env prod
```

- [x] **11. Recommended: turn on audit logs for Secret Manager** (done by Adam, 2026-11-15; 2 min), so a read of the database URL or the encryption key leaves a record. They're off by default. In the console: **IAM & Admin → Audit Logs**, find **Secret Manager API**, tick **Data Read**, **Save**. At this volume it costs nothing.

## Day to day

Nothing to do. Every morning the 06:17 snapshot runs and hands over to `daily`, which hands over to `weekly`, and the phone hears about problems. To read this morning's report before the phone app exists:

```bash
scripts/fh-prod.sh report open
```

**What the counts in the run summaries mean.** `daily` runs first, so its summary shows everything new since the last load: box scores from last night's games, and the projection rows from every snapshot set saved since (on 2026-11-16: 192 box scores and 4,482 projection rows). `weekly` runs two minutes later on the same data, so its summary says 0 new rows. That is correct: loading the same data twice writes nothing (PR #17). A `weekly` run that reports lots of new rows means `daily` didn't load them first, so find out why.

**Look at a run:**

```bash
gcloud run jobs executions list --job daily --region us-east4 --limit 5
```

The log of one run is in the console: **Cloud Run → Jobs → the job → Executions → Logs**.

**Run one by hand** (safe at any hour: a run takes its turn behind another, ingests only what's new, and logs each call once):

```bash
gcloud run jobs execute daily --region us-east4 --wait
```

**Migrate by hand** (the deploy does this on its own; this is for a deploy that stopped at the migration after it's fixed):

```bash
gcloud run jobs execute migrate --region us-east4 --wait
```

## Adding a league later

Same as rollout step 5. For a beta friend (M7) the app will do this with their own cookies; until then it's you at a hidden prompt.

## When something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| **Healthchecks: `daily` is down** (about 07:45) | The report isn't built | Open the job's log. Usually one of the rows below |
| **"Daily report not started: the snapshot is incomplete"**, then `daily` down at 07:45 | One dataset failed, or was saved but looked wrong. The chain stopped on purpose: no report from partial data | The snapshot's alert says which. Once the source is back, re-run the snapshot, which re-fetches what failed and, if everything now passes, restarts the chain: `gcloud run jobs execute snapshot-morning --region us-east4 --wait` |
| **"couldn't start daily"** (or `weekly`) | The snapshot (or daily run) finished and saved everything, but the next job wouldn't start | Start it by hand: `gcloud run jobs execute daily --region us-east4 --wait`. If it keeps happening, check `scripts/gcp-setup.sh show` lists the chain and `fh-jobs` / `fh-reports` still have their start permission |
| **"this morning's snapshot isn't saved yet"** from `daily` or `weekly` (the log says "no complete morning snapshot yet") | Started by hand (or a retry) before the morning snapshot finished, or it was incomplete, or today's snapshot ran an image from before the completion marker existed (rollout day) | Wait for the snapshot, or re-run it (above): that's the better fix, because it restarts the whole chain. To run anyway on what's saved, start the job with its command replaced: `gcloud run jobs execute daily --region us-east4 --args=run,daily --wait`. That `daily` run still hands over to `weekly`, which waits for the same marker and fails without it |
| **"access denied (expired cookies?)"** for an ESPN league | ESPN cookies expired | `scripts/fh-prod.sh league add espn LEAGUE_ID --new-cookies`, then run `daily` by hand |
| **"the database was unreachable"** after 3 attempts | Supabase paused or down, or the URL's password changed | `scripts/fh-prod.sh doctor`. A new password means a new value: `scripts/gcp-secrets.sh fh-database-url` |
| **A deploy that failed at "Deploy the jobs, migrating the database in between"** | The migration failed. The report jobs still run their old image | Read the step's log, fix forward with a new migration, merge it |
| **A `daily` run that exited 3** | It finished with a problem it already told the phone about (a league failed, a check disagreed) | Read the phone alert; the run's log has the detail |

**Rotating the encryption key** means every league's ESPN cookies are typed again (ADR 0007): add the new value with `scripts/gcp-secrets.sh fh-encryption-key`, then `league add ... --new-cookies` for each ESPN league.
