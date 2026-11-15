# ADR 0027: Operating the prod database: migrate on deploy, secrets per process, two accounts

**Status:** Accepted · 2026-11-15 · extends ADR 0025's accounts and secrets

## Decision
- **The deploy migrates the database.** `deploy.yml` deploys a `migrate` Cloud Run job (`fh migrate`, which runs Alembic to head and is safe to repeat) and runs it with `--wait`, **after** the snapshot jobs and **before** the report jobs. A failed migration stops the deploy: the report jobs keep running their old image against the old schema, which still match. Only after it succeeds do they move to code that expects the new tables. The snapshot jobs don't touch the database, so a bad migration never delays them.
- **Prod has no `.env` on the laptop.** `scripts/fh-prod.sh <command>` runs one `fh` command against prod: it reads the database URL, the encryption key and the ntfy topic from Secret Manager into that one process, and nothing is written to disk. It runs only `league`, `report` and `doctor`: the commands that read or edit your own data. The runs and the migration are Cloud Run jobs, because a daily run started from a laptop at 2 p.m. would log calls against games already played. The laptop's `.env` keeps pointing at dev.
- **Two service accounts.** `fh-jobs` runs the snapshot jobs and reads five secrets (R2, ntfy, Healthchecks): public data only. `fh-reports` runs `migrate`, `daily` and `weekly`, and also reads the database URL and the encryption key. Each may also start the next job in the morning chain and only that one (ADR 0028): `fh-jobs` can start `daily`, `fh-reports` can start `weekly`. The job that talks to the public internet and parses its responses can't reach the database or the key that decrypts ESPN cookies, and `tests/unit/test_workflows.py` fails if a change gives it either.
- **The database secret is Supabase's session pooler** (port 5432). The jobs run migrations and a long transaction, and psycopg's prepared statements can break behind the transaction pooler (port 6543), which is for the API's short queries in part 3. `scripts/gcp-secrets.sh` refuses a 6543 URL for this secret.
- **Adding leagues to prod and loading history are runbook steps, not code:** `fh-prod.sh league add ...` (ESPN cookies at a hidden prompt, encrypted with the prod key), then one `ingest` through the `daily` job with its command replaced (`docs/runbooks/prod-database.md`).
- **`fh doctor` names the Supabase region** when the URL is a pooler host, so the region is checked rather than assumed (ADR 0020: the database and the API share a metro).

## Why
The prod database URL and the key are the two values that, together, expose every user's league logins. The fewer places they exist, and the fewer processes that can read them, the better. A prod `.env` on a laptop that also runs scheduled jobs and a code editor is the opposite of that. Reading them into one process for one command is not. (Secret Manager can record who read a secret, but only if Data Access audit logs are turned on for it, and they're off by default: [the runbook](../runbooks/prod-database.md) has a step to turn them on.)

Migrating inside Google keeps the database credentials out of GitHub's runners: a compromised workflow step could otherwise reach the database. And running the migration as a job with `--wait` gives the ordering for free, with no tool that has to be installed or a branch that has to be remembered.

## Rejected
- **Migrating from the laptop** (`DATABASE_URL=… alembic upgrade head`): prod's URL on disk, and a step that depends on someone remembering it after every merge.
- **Migrating from GitHub Actions** (the URL is already a `prod` secret there): it works, and it puts the production database in reach of the runner. The secret in GitHub's `prod` environment (`DATABASE_URL`, `FH_ENCRYPTION_KEY`) is no longer used by anything; remove it once the rollout works.
- **One account for every job:** simpler, but the snapshot job would hold the keys to league logins for no reason.
- **A shared `.env.prod` file:** see Why.
- **Letting `fh-prod.sh` run anything:** one slip, `fh-prod.sh run daily` at the wrong hour, writes calls to the registry that can never be edited (ADR 0005).

## Consequences
- **A migration is part of every deploy that has one**, and runs before anyone can look at it, so migrations have to be safe to run on a live database: additive first, destructive in a later release. CI migrates an empty database from the built image on every PR that touches the image (`container.yml`), and `alembic check` catches drift.
- **Rolling back code doesn't roll back the schema.** Migrations are additive while the jobs are young; a bad one is fixed forward with a new migration.
- **The deployer can run `migrate` and wait for it** only if its job-level role allows `run.jobs.run` and reading the execution. The first deploy proves it; the fallback is `gcloud run jobs execute migrate` by hand after a merge with a migration.
- **A forgotten rotation:** the encryption key lives in Secret Manager and Bitwarden. Losing both means every league's ESPN cookies are typed again (ADR 0007), not that anything is exposed.
