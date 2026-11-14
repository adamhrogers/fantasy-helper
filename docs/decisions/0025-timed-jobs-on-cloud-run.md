# ADR 0025: Timed jobs run on Cloud Run Jobs, started by Cloud Scheduler

**Status:** Accepted · 2026-11-14 · changes where ADR 0023's snapshots run (its R2 store stays)

## Decision
- **Every timed job is a Cloud Run job** in `us-east4`, the same region as the API, started by **Cloud Scheduler** on the minute in `America/New_York`. Snapshots come first, as `snapshot-morning` (06:17) and `snapshot-evening` (17:17). Part 2's daily and weekly reports follow on the same pattern.
- **One Cloud Run job per scheduled task, with a fixed command.** The scheduler's account (`fh-scheduler`) can only start those jobs (`run.invoker`, granted job by job), never change what they run. Jobs run 1 task, have a 30-minute timeout, and use **no retries**: the snapshot retries inside itself, and a whole rerun would fetch everything twice.
- **The image is built and deployed by GitHub Actions** (`.github/workflows/deploy.yml`) on every push to `main` that touches the code, every Monday (because Dependabot's merges don't start push workflows, ADR 0024), and by hand. The job definitions live in that workflow, so a job's command, timeout or secrets change by review.
- **No credential is stored anywhere for deploys.** GitHub proves who it is with a short-lived OIDC token, and Google accepts it only from this repository's `main` branch (Workload Identity Federation, `scripts/gcp-setup.sh`). The deployer (`fh-deployer`) can push to one Artifact Registry repository and update the two jobs, and nothing else.
- **Secrets live in Secret Manager**, readable by the jobs' account (`fh-jobs`) only, one secret at a time. Adam adds the values at a hidden prompt (`scripts/gcp-secrets.sh`). Cloud Run gets **its own R2 token**, following ADR 0023's one-key-per-machine rule.
- **Healthchecks.io watches every scheduled run** (`observability/healthchecks.py`). A run pings `start`, then `success` once it has finished and reported its own outcome, or `fail` if it stopped without reporting. Healthchecks alerts the prod ntfy topic when a run is late, missing, stuck or failed. Only the cloud jobs hold the ping key.
- **The infrastructure is a script, not clicks:** `scripts/gcp-setup.sh infra | schedule | show`, idempotent and reviewed like code. The project ID and other identifiers live in the `prod` environment's variables and Bitwarden, not in this public repo.

## Why
For the first week of cloud snapshots (2026-11-07 to 11-14), GitHub's scheduler started every run hours late. Mornings ran at 11:40–16:30 instead of 06:17, and evenings at 20:00–23:10 instead of 17:17, often after tip-off. None was dropped. A morning report built on that scheduler would reach Adam in the afternoon. GitHub documents that scheduled runs can be delayed under load, and there's no paid tier that fixes it.

Cloud Scheduler is built to fire on time, and it understands time zones, so the two-entries-per-set workaround for daylight saving (`pipeline/cron.py`) isn't needed. Cloud Run Jobs reuses everything part 3 needs anyway: the Google Cloud project, a Docker image, Secret Manager and keyless deploys. architecture.md §11 already named this as the fallback if Actions ran late.

One job per task keeps the scheduler's permission at "start this job". Overriding a job's command would need a broader role.

Treating a reported failure as `success` for the watchdog avoids double alerts: a degraded snapshot has already sent its own failure to the phone. The watchdog's job is the runs that can't speak for themselves.

## Rejected
- **Staying on GitHub's scheduler:** see Why.
- **An external cron (Cloudflare Worker, Cloud Scheduler) that starts the GitHub workflow:** that needs a long-lived GitHub token that can start workflows, stored outside GitHub, for a public repo. The work would also still queue for a GitHub runner.
- **Keeping the laptop as the scheduler:** a sleeping or closed Mac runs late or not at all, and M3's exit test is "with the laptop closed".
- **Cloud Functions, or a Cloud Run service with an HTTP endpoint:** jobs fit run-to-completion work, have no public endpoint, and allow long timeouts.
- **Terraform:** a second tool and its state file for about a dozen resources. A reviewed, idempotent gcloud script is enough at this size.
- **Granting the deployer `run.developer` on the whole project:** the script creates the jobs first, with a placeholder image, so the deployer can be granted the role on those two jobs only.

## Consequences
- **Cost:** Cloud Scheduler's free tier covers 3 jobs per billing account, then about $0.10 per job per month. Part 2's daily and weekly jobs make four. Cloud Run Jobs, Secret Manager (5 secrets), Artifact Registry (a cleanup policy keeps 5 images) and Logging stay inside their free tiers. The $5 budget alert stays.
- **A new job** means a `deploy` line in `deploy.yml`, an entry in `JOBS` in `scripts/gcp-setup.sh`, and a Healthchecks schedule. `tests/unit/test_workflows.py` checks that the first two and the secret names agree.
- **A broken image can't replace a working one silently.** The deploy builds the image and starts it before updating the jobs, so a failed build leaves the last good image in place, and GitHub emails Adam. Rolling back means pointing the jobs at an earlier image tag ([runbooks/scheduled-jobs.md](../runbooks/scheduled-jobs.md)).
- **Dependabot also updates the base images** (the `docker` ecosystem, pinned digests). The container build isn't a required check, so a bad base image can merge. The Monday deploy then fails before touching the jobs.
- **During the overlap there are three copies:** Cloud Run (on time), GitHub (late) and the laptop (on time, local disk). Cloud Run and GitHub both write to R2. That's harmless: keys never collide, Sleeper's player list is fetched once a day by whichever runs first, and projections gain a second capture. The GitHub schedule and the laptop retire together, after four on-time Cloud Run runs in a row with the Healthchecks schedules live.
- **Logs** are in Cloud Logging (JSON lines, parsed), not in GitHub's run pages.
