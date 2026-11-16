# Accounts and secrets: what exists and where

**Last checked:** 2026-11-07, by Claude (Google Cloud, Healthchecks and the last `prod` secret on 2026-11-14; Secret Manager's seven secrets and the five Cloud Run jobs on 2026-11-15, with `gcloud`, counting versions only), against `gh secret list` (names and dates only) and the names in the laptop's `.env`. No value was read. Claude updates this page whenever an account or secret is added, moved or rotated.

Values live in **Bitwarden** and in the places listed below, never in this repo or the chat. How each account was set up, step by step: [m3-account-setup.md](m3-account-setup.md).

## Accounts

| Service | What it does for us | Since |
|---|---|---|
| **GitHub** `adamhrogers/fantasy-helper` (public) | Code, CI, the deploy to Google Cloud (`deploy.yml`, keyless), a manual fallback for the snapshot (**Run workflow**; its schedule was dropped 2026-11-16), a few production secrets in the `prod` environment (usable only from `main`), Dependabot. `main` has a ruleset that requires CI (ADR 0024) | M0. `prod` and the ruleset: 2026-11-07 |
| **Supabase** | Postgres databases, and from part 3 the app's sign-in. Two projects: dev (`us-east-1`), and prod, created per setup §2 as `fantasy-helper-prod` in `us-east-1`; the deploy migrated it to 0006 on 2026-11-15, and `scripts/fh-prod.sh doctor` names its region. The free plan allows two, so both slots are used | Dev: M0. Prod: 2026-11-07 |
| **Cloudflare** | R2 bucket `fantasy-helper-raw`: raw snapshots and league responses, later the season archives and backups. Pages hosts the phone app from part 4 | 2026-11-07 |
| **LangSmith** | Traces of every model call (rule 3), and the doctor's smoke trace | M0 |
| **ntfy** (`ntfy.sh`, no account) | Ops alerts to the iPhone: a **dev** topic for the laptop, a **prod** topic for the cloud jobs, Healthchecks and GitHub | Dev: M0. Prod: 2026-11-07 |
| **Anthropic** | The Claude API, for the app only. Claude Code doesn't use this key (rule 6) | M0 |
| **Bitwarden** | Holds every value on this page | 2026-11-07 |
| **Google Cloud** | Project `fantasy-helper` (ID in Bitwarden), region `us-east4`: the Cloud Run jobs and Cloud Scheduler (live since 2026-11-14), Cloud Run for the API (part 3), Artifact Registry, Secret Manager. Billing on the open billing account with a $5 budget. `gcloud` is installed from Google's installer in `~/google-cloud-sdk`, not Homebrew | 2026-11-14 |
| **Healthchecks.io** | Watches every scheduled run (ADR 0025): each Cloud Run job pings start, then success or fail, since 2026-11-14. Checks: `snapshot-morning`, `snapshot-evening`, and `daily` and `weekly` from their first run. Down alerts go to the prod ntfy topic. Schedules are set only by `scripts/healthchecks-setup.sh`, with a read-write API key that lives in Bitwarden and is typed at its hidden prompt | 2026-11-14 |

## Secrets

| Secret | Laptop `.env` (dev) | GitHub repo secret (CI) | GitHub `prod` | If it leaks |
|---|---|---|---|---|
| `DATABASE_URL` | dev database | | prod database, session pooler (2026-11-07) | Reset that project's database password in Supabase, then update the URL |
| `FH_ENCRYPTION_KEY` | dev key | | prod's own key (2026-11-07) | Make a new one with `uv run fh keygen`; ESPN cookies encrypted with the old one are typed in again (ADR 0007) |
| `FH_NTFY_TOPIC` | dev topic | | prod's own topic (2026-11-07) | Anyone with a topic can read and send on it. Make a new one with `uv run fh keygen --topic`, set it, resubscribe on the phone |
| `FH_R2_ENDPOINT`, `FH_R2_ACCESS_KEY_ID`, `FH_R2_SECRET_ACCESS_KEY` | the same token as prod, for now | | 2026-11-07 | Cloudflare → R2 → Manage API tokens: create a token, set it, then delete the old one. The laptop gets its own token at the end of the cutover ([cloud-snapshots.md](cloud-snapshots.md), step 5) |
| `LANGSMITH_API_KEY` | ✓ | ✓ (2026-11-06) | ✓ (2026-11-07) | Revoke it in LangSmith, set the new one in all three places |
| `FH_ANTHROPIC_API_KEY` | ✓ | | not here: Secret Manager, before M4 | Revoke it in the Anthropic Console |
| `FH_HEALTHCHECKS_PING_KEY` | | | 2026-11-14 | Regenerate it in the project's settings |

**Google Secret Manager** (the Cloud Run jobs, ADR 0025 and 0027; the first five filled 2026-11-14, [scheduled-jobs.md](scheduled-jobs.md) setup, step 3; the last two in [prod-database.md](prod-database.md), step 2). Two accounts read them: `fh-jobs` (the snapshot jobs) reads the **first five only**, and `fh-reports` (`daily`, `weekly`, `migrate`) reads all seven. Values are added only with `scripts/gcp-secrets.sh` at a hidden prompt:

| Secret | Holds | If it leaks |
|---|---|---|
| `fh-r2-endpoint`, `fh-r2-access-key-id`, `fh-r2-secret-access-key` | **Cloud Run's own R2 token** (`cloud-run-jobs`), not the laptop's or GitHub's | Create a new token, add it with `scripts/gcp-secrets.sh`, delete the old token |
| `fh-ntfy-topic` | The prod topic (the same as GitHub's) | As `FH_NTFY_TOPIC` above, then update it in both places |
| `fh-healthchecks-ping-key` | The same key as GitHub `prod` | Regenerate it, then update it in both places |
| `fh-database-url` | Prod's database, Supabase's **session pooler** (port 5432), for the report jobs and `scripts/fh-prod.sh` | Reset the database password in Supabase, then `scripts/gcp-secrets.sh fh-database-url` |
| `fh-encryption-key` | Prod's encryption key (the same value as GitHub `prod`'s `FH_ENCRYPTION_KEY` until that's removed) | Every ESPN cookie is typed again (ADR 0007): see the rotation note in [prod-database.md](prod-database.md) |
| `fh-readonly-database-url` | The `fh_readonly` login's URL (read-only, ADR 0032). **No job reads it:** only your own `gcloud` does, through `scripts/fh-sql.sh` | `scripts/readonly-setup.sh` again (it rotates the password) |

**After part 2's rollout, GitHub's `prod` environment no longer needs `DATABASE_URL` or `FH_ENCRYPTION_KEY`:** the report jobs read them from Secret Manager, and the deploy migrates from inside Google (ADR 0027). They're removed in [prod-database.md](prod-database.md), step 10.

**The `prod` environment's variables** (identifiers, not secrets; set by `scripts/gcp-setup.sh infra`): `GCP_PROJECT`, `GCP_DEPLOYER_SA` and `GCP_WIF_PROVIDER`. They're kept out of this public repo. Deploys hold no key: GitHub signs in to Google with a short-lived token, accepted only from `main`.

**Only in Bitwarden** (not in GitHub yet): the prod database password, the prod transaction pooler URL (port 6543, for the API), and prod's project URL and publishable key. The last two go into the phone app in part 4: they aren't secret, but they stay out of the public repo. Each account's login and two-factor recovery codes belong in Bitwarden too.

**Not in any environment variable:** ESPN cookies (`espn_s2`, `SWID`) and league IDs live in the database, with the cookies encrypted by that environment's `FH_ENCRYPTION_KEY` (rule 5, ADR 0007). Dev has them. Prod gets them in part 2: you type them once at a hidden prompt.

**Set in the workflows, not secret:** `FH_ENV=prod`, `FH_RAW_STORE=r2` and `FH_LOG_JSON=true`, in `.github/workflows/deploy.yml` (on every Cloud Run job, with `FH_NEXT_JOB` on the two that hand over) and `.github/workflows/snapshot.yml`.

**Good to know:** GitHub prints an environment's variables in a run's log, and this repository's logs are public, so the Google Cloud project ID and number can be read there. They identify the project and grant nothing; hiding them means storing them as `prod` secrets instead ([m3-plan.md](../m3-plan.md), decision 6).

## Coming in M3 parts 3 to 5 (planned names; [m3-plan.md](../m3-plan.md))

| Secret (Secret Manager) | Holds | Read by | Part |
|---|---|---|---|
| `fh-api-database-url` | Prod's database, the **transaction pooler** (port 6543) | `fh-api` | 3 |
| `fh-supabase-url` | Prod's Supabase project URL (not secret; kept out of the repo) | `fh-api` | 3 |
| `fh-app-origins` | The phone app's address, for the API's allow-list | `fh-api` | 3, set in 4 |
| `fh-vapid-private-key` | The key that signs push notifications | `fh-reports` | 5 |
| `fh-vapid-public-key` | Its public half | `fh-api` | 5 |
| The backups bucket's access key ID and secret | A second R2 token, for `fantasy-helper-backups` only | `fh-backup` | 5 |
| `fh-anthropic-api-key`, `fh-langsmith-api-key` | The Claude key (with a spend limit) and the LangSmith key, for the report jobs | `fh-reports` | Before M4 |

The phone app itself is built by Cloudflare with three build variables (the API's address, the Supabase project URL and the **publishable** key, `sb_publishable_...`). Supabase is retiring the older `anon` key at the end of 2026: if that's what was saved on 2026-11-07, copy the publishable one from the project's API keys page before part 4.

## Checking without seeing values

```bash
gh secret list --env prod
```

```bash
gh secret list
```

Both print names and dates only. For the laptop, `uv run fh doctor` checks each setting without printing it.
