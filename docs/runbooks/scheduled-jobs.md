# Scheduled jobs on Google Cloud (Cloud Run Jobs + Cloud Scheduler)

**Written:** 2026-11-14. **Design:** ADR 0025. **Who:** Claude runs the setup and checks. Adam adds the secret values, creates one R2 token, and sets two Healthchecks schedules.

| Job | Runs | Command | Watchdog check |
|---|---|---|---|
| `snapshot-morning` | 06:17 Eastern | `fh snapshot --set morning` | `snapshot-morning` |
| `snapshot-evening` | 17:17 Eastern | `fh snapshot --set evening` | `snapshot-evening` |

Part 2 adds the daily and weekly reports here.

## First-time setup (after the PR merges)

- [ ] **1. Claude:** create the cloud side. This makes the image repository, three service accounts, five empty secrets, keyless GitHub deploys, the two jobs (with a placeholder image), and the `prod` environment's variables:

```bash
scripts/gcp-setup.sh infra
```

- [ ] **2. Adam: a separate R2 token for Cloud Run** (2 min). Cloudflare → **R2 → Manage API tokens → Create API token**: **Object Read & Write**, scoped to `fantasy-helper-raw`, named `cloud-run-jobs`. Save its access key ID and secret in Bitwarden. The endpoint is the same as before.
- [ ] **3. Adam: the secret values** (5 min). In your own Terminal app, from `~/projects/fantasy-helper`, paste each value from Bitwarden at the hidden prompt. Nothing is shown or saved on the laptop:

```bash
scripts/gcp-secrets.sh
```

- [ ] **4. Claude:** deploy (**Actions → Deploy → Run workflow**), then create the schedules:

```bash
scripts/gcp-setup.sh schedule
```

- [ ] **5. Claude:** run the evening job once by hand and check it end to end: the job succeeds, a new evening set is in R2, the prod topic gets "Evening snapshot succeeded", and a `snapshot-evening` check appears in Healthchecks.

```bash
gcloud scheduler jobs run snapshot-evening --location us-east4
```

- [ ] **6. Adam: the Healthchecks schedules** (2 min). Each check appears after its first ping. Open it, then **Change Schedule → Cron**:
  - `snapshot-morning`: `17 6 * * *`, time zone `America/New_York`, grace **30 minutes**.
  - `snapshot-evening`: `17 17 * * *`, time zone `America/New_York`, grace **30 minutes**.

Then the overlap in [cloud-snapshots.md](cloud-snapshots.md) continues, with Cloud Run as the cloud copy.

## Day to day

Nothing to do. When something goes wrong:
- **The run's own alerts** (degraded, failed, crashed) come from the app on the prod topic, as before.
- **Healthchecks** alerts on the prod topic when a run is **late, missing or crashed without a word**.

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

Create the new value where it comes from (Cloudflare, ntfy, Healthchecks). Then add it as a new version; the next run uses it:

```bash
scripts/gcp-secrets.sh fh-r2-secret-access-key
```

Then revoke the old one at its source.

## Adding a scheduled job

Three places, kept in step by `tests/unit/test_workflows.py`:
1. A `deploy <job> <args>` line in `.github/workflows/deploy.yml`.
2. An entry in `JOBS` in `scripts/gcp-setup.sh`, then `scripts/gcp-setup.sh infra` and `scripts/gcp-setup.sh schedule`.
3. The job's Healthchecks schedule, after its first ping.
