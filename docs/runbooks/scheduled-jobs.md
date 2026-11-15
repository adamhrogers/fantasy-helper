# Scheduled jobs on Google Cloud (Cloud Run Jobs + Cloud Scheduler)

**Written:** 2026-11-14. **Design:** ADR 0025. **Who:** Claude runs the setup and checks. Adam adds the secret values, creates one R2 token, and sets the Healthchecks schedules. The report jobs and the migration (2026-11-15, ADR 0026 and 0027) are in [prod-database.md](prod-database.md).

| Job | Runs | Command | Watchdog check |
|---|---|---|---|
| `snapshot-morning` | 06:17 Eastern | `fh snapshot --set morning` | `snapshot-morning` |
| `snapshot-evening` | 17:17 Eastern | `fh snapshot --set evening` | `snapshot-evening` |
| `daily` | 06:45 Eastern | `fh run daily --require-snapshot-today` | `daily` (grace 60 min: the 07:45 alert) |
| `weekly` | 07:00 Eastern | `fh run weekly --require-snapshot-today` | `weekly` |
| `migrate` | every deploy | `fh migrate` | none: the deploy waits for it |

The times, and each check's grace, are in [`scripts/jobs.conf`](../../scripts/jobs.conf): Cloud Scheduler's entries and Healthchecks' schedules both come from it. The last three jobs are set up in [prod-database.md](prod-database.md).

## First-time setup (after the PR merges)

- [x] **1. Claude:** create the cloud side (done 2026-11-14). This makes the image repository, three service accounts, five empty secrets, keyless GitHub deploys, the two jobs (with a placeholder image), and the `prod` environment's variables:

```bash
scripts/gcp-setup.sh infra
```

- [x] **2. Adam: a separate R2 token for Cloud Run** (done 2026-11-14). Cloudflare → **R2 → Manage API tokens → Create API token**: **Object Read & Write**, scoped to `fantasy-helper-raw`, named `cloud-run-jobs`. Save its access key ID and secret in Bitwarden. The endpoint is the same as before.
- [x] **3. Adam: the secret values** (done 2026-11-14). In your own Terminal app, from `~/projects/fantasy-helper`, paste each value from Bitwarden at the hidden prompt. Nothing is shown or saved on the laptop:

```bash
scripts/gcp-secrets.sh
```

- [x] **4. Claude:** deploy (**Actions → Deploy → Run workflow**), then create the schedules (done 2026-11-14: the deploy ran on PR #14's merge, with the deployer's job-level role, and Cloud Scheduler is offered in `us-east4`, so neither fallback below was needed). Until step 1 has run, Deploy does nothing and says so in a notice, rather than failing, so merging the PR is quiet.

  If the deploy fails with **PERMISSION_DENIED** on a project-level permission (the deployer's role is granted on the two jobs only), grant `roles/run.developer` on the project to `fh-deployer` instead, note it in ADR 0025, and rerun. If `schedule` says Cloud Scheduler isn't offered in `us-east4`, set `SCHEDULER_LOCATION` in the script to the nearest location it lists, and use that location in the commands below.

```bash
scripts/gcp-setup.sh schedule
```

- [x] **5. Claude:** run the evening job once by hand and check it end to end (done 2026-11-14: evening 4/4 and morning 9/9 into R2, with Sleeper's player list reused, notifications sent, and both checks pinged with no errors): the job succeeds, a new evening set is in R2, the prod topic gets "Evening snapshot succeeded", and a `snapshot-evening` check appears in Healthchecks.

```bash
gcloud scheduler jobs run snapshot-evening --location us-east4
```

- [x] **6. Adam: the Healthchecks schedules** (typed in on 2026-11-14 and **saved in UTC**, the website's default, which raised a false "down" alert for the evening snapshot that night). Since 2026-11-15 they're set by `scripts/healthchecks-setup.sh`, from `scripts/jobs.conf`, always in `America/New_York` ([prod-database.md](prod-database.md), step 9). Don't type a schedule into the website.

Then the overlap in [cloud-snapshots.md](cloud-snapshots.md) continues, with Cloud Run as the cloud copy.

## Day to day

Nothing to do. **An occasional double start is expected:** Cloud Scheduler delivers "at least once", and on 2026-11-14 one test start of `snapshot-evening` ran twice, 30 seconds apart (two scheduler attempts, both HTTP 200). Both runs saved full sets under different keys, so nothing is lost or overwritten. If it happens on scheduled runs too, `fh snapshot` gets a guard that skips a set saved minutes earlier.

When something goes wrong:
- **The run's own alerts** (degraded, failed, crashed) come from the app on the prod topic, as before, and say what went wrong.
- **Healthchecks** alerts on the prod topic when a run is **late, missing, stuck, or exits with any failure**. A degraded run therefore alerts twice, on purpose: if the app's own alert couldn't be sent, this one still arrives. The check goes back up on the next clean run.

## Checking

What exists, and how many versions each secret has (never the values):

```bash
scripts/gcp-setup.sh show
```

The latest runs of a job:

```bash
gcloud run jobs executions list --job snapshot-morning --region us-east4 --limit 5
```

A run's log, in the console: **Cloud Run → Jobs → the job → Logs**. The lines are JSON, like the laptop's.

## Running a set by hand

Starts the job exactly as the schedule would. Safe at any hour: nothing is overwritten, and Sleeper's player list is fetched once a day whichever run comes first.

```bash
gcloud scheduler jobs run snapshot-morning --location us-east4
```

## Pausing

Stops the schedule but keeps the job (for example, if a run keeps failing). The laptop keeps saving during the overlap.

```bash
gcloud scheduler jobs pause snapshot-evening --location us-east4
```

```bash
gcloud scheduler jobs resume snapshot-evening --location us-east4
```

## Rolling back to an earlier image

Each deploy tags the image with its commit. List them, then point a job at a previous one. The next deploy from `main` replaces it again, so revert the bad commit too.

```bash
gcloud artifacts docker images list us-east4-docker.pkg.dev/$(gcloud config get project)/fantasy-helper/app --include-tags --limit 5
```

```bash
gcloud run jobs update snapshot-morning --region us-east4 --image <image>:<commit>
```

## Rotating a secret

Create the new value where it comes from (Cloudflare, ntfy, Healthchecks). Then add it as a new version; the next run uses it, and the script disables the older versions so the old value stops working in Google too:

```bash
scripts/gcp-secrets.sh fh-r2-secret-access-key
```

Then revoke the old one at its source. To undo a bad value: `gcloud secrets versions enable <number> --secret <name>`, and disable the new one.

## Adding a scheduled job

Three places, kept in step by `tests/unit/test_workflows.py`:
1. A `deploy <job> <account> <secrets> <args> <timeout> <memory>` line in `.github/workflows/deploy.yml`.
2. A `job|cron|grace` line in `scripts/jobs.conf`, then `scripts/gcp-setup.sh infra` (it creates the job) and, after the first deploy, `scripts/gcp-setup.sh schedule`.
3. `scripts/healthchecks-setup.sh`, which sets the job's check from the same line.
