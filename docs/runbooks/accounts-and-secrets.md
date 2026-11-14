# Accounts and secrets: what exists and where

**Last checked:** 2026-11-07, by Claude (Google Cloud, Healthchecks and the last `prod` secret on 2026-11-14), against `gh secret list` (names and dates only) and the names in the laptop's `.env`. No value was read. Claude updates this page whenever an account or secret is added, moved or rotated.

Values live in **Bitwarden** and in the places listed below, never in this repo or the chat. How each account was set up, step by step: [m3-account-setup.md](m3-account-setup.md).

## Accounts

| Service | What it does for us | Since |
|---|---|---|
| **GitHub** `adamhrogers/fantasy-helper` (public) | Code, CI, the scheduled jobs, production secrets in the `prod` environment (usable only from `main`), Dependabot. `main` has a ruleset that requires CI (ADR 0024) | M0. `prod` and the ruleset: 2026-11-07 |
| **Supabase** | Postgres databases, and from part 3 the app's sign-in. Two projects: dev (`us-east-1`), and prod, created per setup §2 as `fantasy-helper-prod` in `us-east-1` (part 2 confirms both when it migrates prod). The free plan allows two, so both slots are used | Dev: M0. Prod: 2026-11-07 |
| **Cloudflare** | R2 bucket `fantasy-helper-raw`: raw snapshots and league responses, later the season archives and backups. Pages hosts the phone app from part 4 | 2026-11-07 |
| **LangSmith** | Traces of every model call (rule 3), and the doctor's smoke trace | M0 |
| **ntfy** (`ntfy.sh`, no account) | Ops alerts to the iPhone: a **dev** topic for the laptop, a **prod** topic for GitHub | Dev: M0. Prod: 2026-11-07 |
| **Anthropic** | The Claude API, for the app only. Claude Code doesn't use this key (rule 6) | M0 |
| **Bitwarden** | Holds every value on this page | 2026-11-07 |
| **Google Cloud** | Project `fantasy-helper` (ID in Bitwarden), region `us-east4`: Cloud Run for the API (part 3), Artifact Registry, Secret Manager. Billing on the open billing account with a $5 budget. `gcloud` is installed from Google's installer in `~/google-cloud-sdk`, not Homebrew | 2026-11-14 |
| **Healthchecks.io** | Watches for scheduled runs that never start or start late (part 5). Down alerts go to the prod ntfy topic. No checks until part 5 wires the pings | 2026-11-14 |

## Secrets

| Secret | Laptop `.env` (dev) | GitHub repo secret (CI) | GitHub `prod` | If it leaks |
|---|---|---|---|---|
| `DATABASE_URL` | dev database | | prod database, session pooler (2026-11-07) | Reset that project's database password in Supabase, then update the URL |
| `FH_ENCRYPTION_KEY` | dev key | | prod's own key (2026-11-07) | Make a new one with `uv run fh keygen`; ESPN cookies encrypted with the old one are typed in again (ADR 0007) |
| `FH_NTFY_TOPIC` | dev topic | | prod's own topic (2026-11-07) | Anyone with a topic can read and send on it. Make a new one with `uv run fh keygen --topic`, set it, resubscribe on the phone |
| `FH_R2_ENDPOINT`, `FH_R2_ACCESS_KEY_ID`, `FH_R2_SECRET_ACCESS_KEY` | the same token as prod, for now | | 2026-11-07 | Cloudflare → R2 → Manage API tokens: create a token, set it, then delete the old one. The laptop gets its own token at the end of the cutover ([cloud-snapshots.md](cloud-snapshots.md), step 5) |
| `LANGSMITH_API_KEY` | ✓ | ✓ (2026-11-06) | ✓ (2026-11-07) | Revoke it in LangSmith, set the new one in all three places |
| `FH_ANTHROPIC_API_KEY` | ✓ | | before M4 | Revoke it in the Anthropic Console |
| `FH_HEALTHCHECKS_PING_KEY` | | | 2026-11-14 | Regenerate it in the project's settings |

**Only in Bitwarden** (not in GitHub yet): the prod database password, the prod transaction pooler URL (port 6543, for the API), and prod's project URL and publishable key. The last two go into the phone app in part 4: they aren't secret, but they stay out of the public repo. Each account's login and two-factor recovery codes belong in Bitwarden too.

**Not in any environment variable:** ESPN cookies (`espn_s2`, `SWID`) and league IDs live in the database, with the cookies encrypted by that environment's `FH_ENCRYPTION_KEY` (rule 5, ADR 0007). Dev has them. Prod gets them in part 2: you type them once at a hidden prompt.

**Set in the workflow, not secret:** `FH_ENV=prod`, `FH_RAW_STORE=r2` and `FH_LOG_JSON=true`, in `.github/workflows/snapshot.yml`.

## Checking without seeing values

```bash
gh secret list --env prod
```

```bash
gh secret list
```

Both print names and dates only. For the laptop, `uv run fh doctor` checks each setting without printing it.
