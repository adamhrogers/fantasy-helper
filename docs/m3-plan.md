# M3, the rest: a phased plan

**Written:** 2026-11-15, right after PR #16 (part 2) merged and deployed. **For:** whichever session picks M3 up next, on any model. **Read first:** [session-handoff.md](session-handoff.md) (how we work, what exists), then this.

This is the plan for everything between "part 2 is merged" and "M3 is done": six phases, in order, each with what to build, what Adam does, how to check it, and the traps already known. Every design choice below was made on purpose and has its reason next to it. Don't re-decide them without a new fact; if a fact has changed, say so, change the plan in the same PR, and carry on.

## Status (update this table in every PR)

| Phase | What | Status |
|---|---|---|
| **0** | Finish part 2's rollout: leagues in prod, history loaded, first chained morning | **Done (2026-11-16).** All 11 runbook steps are ticked; the chain ran on its own at 06:17 on 2026-11-16 (snapshot, `daily`, `weekly` all succeeded by 06:20) |
| **1** | End the snapshot overlap: retire launchd and the GitHub schedule | **Done 2026-11-16**, laptop steps included (the last copies uploaded, the laptop has its own R2 key, `FH_RAW_STORE=r2`). He ended it after 3 of 4 on-time runs; the Mac's schedule is removed and GitHub's is dropped (PR #19) |
| **2** | Part 3: the API on Cloud Run, sign-in checked, reports served | **Done 2026-11-16.** Merged (PR #26) and deployed; Adam's cloud setup finished and `scripts/api-smoke.sh` passed every check against the live service: his password sign-in, his 3 leagues, his saved reports, 401 without a token, sign-ups refused. The first smoke run found two things, fixed in PR #28: Cloud Run answers `/healthz` itself, so the health route is `/livez`; and a bare `postgres` user in the API's database secret made every signed-in call fail, so `gcp-secrets.sh` now refuses a pooler URL with no project ref |
| **3** | Part 4: the phone app | **Done 2026-11-22.** Merged (PR #32) and live on Cloudflare Workers at a `workers.dev` address; Adam added it to the iPhone's Home Screen, signed in with his password and sees his leagues' cards. The API allows the app's address (`fh-app-origins`). The security headers on the live site name exactly the API and Supabase addresses. Not yet checked: that an update arrives (deploy twice and watch the "new version" bar) and the offline copy. Adam has a list of improvements to the screens, to be planned as their own work |
| **4** | Part 5a: push ("your report is ready") | **Built 2026-11-22; PR open, not merged.** Migration 0008, `delivery/push.py` and `pipeline/notify.py` (after commit, once, never fails a run), `fh push test`, `scripts/vapid-setup.sh`, the cloud side (the private key readable by the report jobs only), Enable notifications / Turn off in the app, the service worker's `push` and `notificationclick`, ADR 0030, runbook Part 3. Tested against a fake push service that decrypts the message and checks the VAPID token, and against Postgres. **Not yet seen: a real push on the iPhone**; the first one may surface surprises (Apple's answers can't be reproduced in a test). **Adam's steps:** [phone-app.md](runbooks/phone-app.md) Part 3 |
| **5** | Part 5b: backups, locks, loose ends, the exit test | Not started |
| **Lane** | The daily "what happened" cards for every league (decision 8; roadmap direction; ADR 0033), beside Phases 2 to 5, engine only | **Merged 2026-11-16** (PR #27, no migration; touches no API, app or schema file). The lock button (decision 9) and lock-in v2 are still to do |

Phases 0 and 1 are rollout work, not code. Phases 2 to 5 are one branch and one PR each, merged by Adam, because the deploy runs only from `main`.

## How to use this plan

1. **Check the status table against reality before trusting it.** `git log origin/main --oneline -10`, `gh pr list`, `scripts/gcp-setup.sh show`. A session can end between a merge and a doc update.
2. **Start each phase by restating its plan to Adam in a few lines and waiting for "go".** He approves plans before code, and approves every commit, push, PR and installed job separately (handoff §3).
3. **Check each outside fact the phase depends on before building on it.** The ones this plan rests on were read from the vendors' own docs on 2026-11-15 and are listed in [Outside facts](#outside-facts-checked-2026-11-15). Vendor dashboards and flags change; a five-minute re-read is cheaper than a wrong build.
4. **Stop and ask** when a step would touch prod data in a way this plan doesn't name, when a secret would have to pass through the chat, or when a "Decision for Adam" below is still open and the phase needs it.
5. **End each phase** by updating the status table, the handoff (§2 and §7), the roadmap's M3 status and the runbook the phase touched, in the same PR as the code.

**Every code phase (2 to 5) rolls out in the same order,** because the deployer can update what exists but never create it, and a merge that touches `src/` deploys within minutes:

1. **Before merging, from the PR's branch:** `scripts/gcp-setup.sh infra`. It only adds (accounts, empty secrets, placeholder jobs or the service, permissions).
2. **Adam fills every new secret** with `scripts/gcp-secrets.sh <name>`. A deploy that mounts a secret with no value fails.
3. **Merge.** The deploy builds the image, updates the snapshot jobs, runs any new migration against prod and waits for it, then moves the report jobs and the service. Watch it (`gh run watch`), and read the migration's line in the `migrate` job's log.
4. **After the first deploy,** if the phase added a scheduled job: `scripts/gcp-setup.sh schedule`, then Adam runs `scripts/healthchecks-setup.sh`.
5. **Check with commands that only read** (`scripts/gcp-setup.sh show`, executions lists, job logs), then update the docs.

Part 2 went this way on 2026-11-15 and its first deploy passed without a fallback.

## Where things stand, update of 2026-11-16 (read after the 06:17 chain)

Each line was read from the system itself, not from a doc. The section below it is the record from the day before.

- **The morning chain ran on its own.** `snapshot-morning` started at 06:17:05 Eastern (22 seconds, 9 of 9 datasets), started `daily` (06:17:30 to 06:19:10), which started `weekly` (06:19:10 to 06:20:24). All three succeeded, with no league errors. Nobody pressed anything: this is the proof of the hand-over and of the permissions, from the scheduler.
- **The overlap count was 3 of 4, and Adam ended it there** (2026-11-16): 2026-11-15 06:17 and 17:17, and 2026-11-16 06:17, all on the minute. He removed the Mac's schedule and PR #19 dropped GitHub's, so Cloud Run is the only timer. The Healthchecks schedules are set by the script, and all four checks showed recent, on-time pings.
- **What the run summaries' numbers mean:** `daily` loaded 192 box scores and 4,482 projection rows (everything new since the last load: Sunday's games, and three snapshot sets, two of them GitHub's late copies and one this morning's). `weekly` then loaded 0 and 0, because `daily` had just loaded it all. That is the designed behavior, and PR #17's fix at work: loading the same data twice writes nothing.
- **What the weekly report said** (from the job's log): the ESPN points league got a win chance, a startable-games card, three add/drop suggestions and a below-replacement card; the lock-in league got a win chance (a fresh week has no locks to hide), a games card and a below-replacement card; the categories league got only the games card, as expected until P9.
- **Runbook steps 7, 9, 10 and 11 are done** (the secrets are out of GitHub's `prod` environment).
- **PR #17 (box-score upserts write only changed rows) merged on 2026-11-15** (`3e9eff9`). The database was 45 MB of 500 MB after the history load, about 12 MB of it bloat from re-saving unchanged box scores; `VACUUM FULL player_game_stats` reclaims it (Adam's command; not confirmed run). Details: [session-handoff.md](session-handoff.md) §7.
- **A finding, found and fixed (2026-11-16):** one day's startable count in the weekly report was one lower than the league's real lineup. The cause was the engine's slot filling (a flexible player took a slot that left another player nowhere to start), not missing data; `fill_day` now finds the best full lineup. Details: [session-handoff.md](session-handoff.md) §7, "Found on 2026-11-16". Related: the weekly report now names who each day's count left out and why (PR #21).

## Where things stand (as first checked 2026-11-15, about 09:45 Eastern)

Each line was read from the system itself, not from a doc. Superseded where the update above says so.

- **PR #16 merged** at 09:27 Eastern (`f7544b2`). CI on `main` is green.
- **The deploy that followed succeeded** (Actions run 43724460526, 95 seconds). It answered the two questions the last session left open:
  - **The deployer can run the migration and wait for it.** Its job-level role was enough: `gcloud run jobs execute migrate --wait` ran inside the deploy, and the job's own log says `migrated an empty database to 0006`. Prod is at head. No fallback was needed.
  - **The chain's permissions were accepted.** All five jobs deployed with their accounts, `FH_NEXT_JOB` is set on `snapshot-morning` (to `daily`) and `daily` (to `weekly`), and the job policies show `fh-jobs` may start `daily` and `fh-reports` may start `weekly`, and nothing broader. **Not proven yet:** the hand-over itself, which first happens when a morning snapshot on the new image completes (Phase 0, step 6).
- **All five jobs run the merged image.** `daily` and `weekly` have never run. Seven secrets each have one enabled version.
- **Cloud Scheduler has exactly two entries** (`snapshot-morning` 06:17, `snapshot-evening` 17:17, `America/New_York`, both enabled). Nothing schedules `daily` or `weekly`.
- **The first scheduled Cloud Run morning was on time:** 2026-11-15, started 06:17:05 Eastern, 9/9 datasets, 26 seconds. That is run 1 of the 4 the overlap needs. It ran the image from before the merge, so **it wrote no completion marker for 2026-11-15.**
- **Prod has no user and no leagues yet.** Nobody has typed the ESPN cookies in.
- **Not checked:** `scripts/fh-prod.sh doctor` (rollout step 4). The app's permission system blocked Claude's session from reading prod secrets, so Adam runs it, or allows it. The migration is confirmed by the job log; the doctor would add the Supabase region.

## The order, and why

```
Phase 0  part 2 rollout ──► Phase 1  end the overlap          (rollout; days, not code)
   │
   └──► Phase 2  API ──► Phase 3  phone app ──► Phase 4  push ──► Phase 5  backups + exit test
```

- **Phase 0 first** because the morning chain starts on its own at 06:17 on 2026-11-16 whether prod is ready or not (see the deadline below), and because every later phase reads reports that only exist once it's done.
- **Phase 1 needs only time** (four on-time runs) and Phase 0's step 9. It doesn't block Phase 2.
- **API before app** because the app's types are generated from the API's schema, and the app can't be tested on the phone without a real API.
- **App before push** because iOS only allows push for a web app installed to the Home Screen, and the permission prompt has to come from a tap inside it.
- **Backups last** only because nothing else waits on them. If a phase slips, pull Phase 5's backup forward: prod's registry has no copy until it exists.

**A lane that can run beside Phases 3 to 5:** M4's brain (`brain/`, `mcp_server/`) shares no files with `web/`, `api/` or `delivery/`. See [After M3](#after-m3).

---

## Phase 0: finish part 2's rollout

**Goal:** prod has Adam's user, three leagues and all history; one full morning chain has run; the reports open. The steps are in [runbooks/prod-database.md](runbooks/prod-database.md); this section is the order and the traps.

**Deadline: 06:17 Eastern on 2026-11-16.** That's the first scheduled snapshot on the new image. If it completes, it starts `daily`. With no user in prod, `daily` exits 1 ("no user yet"), pings `fail`, and Healthchecks sends a "down" alert. Nothing is written and `weekly` isn't started, so it's a false alarm, not damage. Finishing steps 4 to 6 on 2026-11-15 avoids it.

| Step | Who | What | Notes |
|---|---|---|---|
| 4 | Adam | `scripts/fh-prod.sh doctor` | Expect `migrations at head (0006), Supabase us-east-1`. Another region: stop and tell Claude (ADR 0020 puts the API next to the database) |
| 5 | Adam | `scripts/fh-prod.sh league add ...` three times | Email, name and time zone once; ESPN cookies once, at the hidden prompt |
| 6a | Claude | Load history: `gcloud run jobs execute daily --region us-east4 --args=ingest --wait` | One transaction: if it hits the 30-minute limit, **nothing** is saved. Run it again with `--task-timeout=60m` |
| 6b | Claude | Start the chain by hand: `gcloud run jobs execute snapshot-morning --region us-east4 --wait` | **Not** `execute daily`: see the trap below |
| 7 | Adam | `scripts/fh-prod.sh report open`, then `... report open weekly` | The first prod reports |
| 9 | Adam | `scripts/healthchecks-setup.sh` | Do it the same day: until it runs, the two snapshot checks may still be on the UTC schedules typed in on 2026-11-14, which raise false "down" alerts |
| 10 | Adam | Remove `DATABASE_URL` and `FH_ENCRYPTION_KEY` from GitHub's `prod` | After one clean scheduled morning (2026-11-16). Checked: no workflow reads either |
| 11 | Adam | Secret Manager audit logs: Data Read on | Recommended, two minutes |

**The trap in step 6 (found 2026-11-15).** The `daily` and `weekly` jobs insist on a completion marker from today, and the 06:17 snapshot on rollout day ran the old image, which wrote none. So `gcloud run jobs execute daily` on rollout day waits 7 minutes and then fails loudly ("this morning's snapshot isn't saved yet"), unless a later run happened to write a marker first (GitHub's late copy does, some hours after 06:17). Starting `snapshot-morning` by hand instead is the right move, and the better test: it saves a fresh set (Sleeper's player list is reused; its saved file is marked valid), writes the marker, and starts `daily`, which starts `weekly`. That is the first live hand-over, so it is also the real answer to "are the chain's permissions accepted".

**What to see after 6b:**
- On the prod topic: `Morning snapshot succeeded` ("1 already saved today by another run"), `Next job started` (daily), the daily run's steps and ending, `Next job started` (weekly), the weekly run's.
- `gcloud run jobs executions list --job daily --region us-east4 --limit 3` and the same for `weekly`: one succeeded run each.
- **If the phone says "couldn't start daily: HTTP 403":** the permission isn't what the policy listing shows. Re-run `scripts/gcp-setup.sh infra` (it only adds), list the policy again (`gcloud run jobs get-iam-policy daily --region us-east4`), then start `daily` by hand: `gcloud run jobs execute daily --region us-east4 --wait`. The snapshot wrote its marker before it tried the hand-over, so the gate passes, and `daily` hands over to `weekly` itself.
- **A hand run in the afternoon logs fewer calls than a morning one.** The tip-off guard only lets a call through while the player's next tip is still ahead. That's correct, not a bug.

**Done when:** one chained morning ran end to end in prod (by hand on 2026-11-15 or on its own on 2026-11-16), both reports open from prod, the four Healthchecks checks show the Eastern schedules, and steps 10 and 11 are ticked. Then tick the runbook and update the handoff. **Met on 2026-11-16.**

## Phase 1: end the snapshot overlap

**Status 2026-11-16: done, including the three steps on Adam's laptop.** Adam ended the overlap after 3 of the 4 on-time runs, without waiting for the fourth. Launchd is removed (`scripts/snapshot-schedule.sh status`: not installed for both) and the GitHub schedule, `pipeline/cron.py` and the `--cron` option are gone (PR #19). Adam then did [runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md) section 5, steps 2 to 4 (`fh raw upload`, a laptop-only R2 key, `FH_RAW_STORE=r2` in `.env`). The text below is the plan as written.

**Goal:** one scheduler, one cloud copy, the laptop free to be closed. Steps: [runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md), section 5.

**Preconditions, both:**
- Four on-time Cloud Run snapshots in a row: 2026-11-15 06:17 (done), 11-15 17:17, 11-16 06:17, 11-16 17:17. Check each with `gcloud run jobs executions list --job snapshot-morning --region us-east4 --limit 3` (and `snapshot-evening`): started within a few minutes of the time, succeeded. A late or failed one restarts the count.
- Healthchecks schedules set by the script (Phase 0, step 9).

**Then:** Adam removes launchd, uploads the laptop's last copies, makes the laptop its own R2 token and sets `FH_RAW_STORE=r2`. Claude opens a small PR that drops the schedule from `snapshot.yml` (keeping **Run workflow**), and removes `pipeline/cron.py`, the `--cron` option, `tests/unit/test_cron.py` and the checks in `tests/unit/test_workflows.py` that tie them together.

**Done when:** `scripts/snapshot-schedule.sh status` says not installed for both jobs, `fh doctor --only raw` on the laptop says R2 is in use, and the PR is merged. Phase 5's laptop-closed test depends on this.

---

## Phase 2 (part 3): the API

**Goal:** `https://<the api>/v1/reports/daily` returns this morning's saved report to Adam's signed-in token, and to nobody else. Branch `m3-part3-api`. **Status 2026-11-16: done** (merged and smoke-checked; see the status table). The steps below became [runbooks/phone-app.md](runbooks/phone-app.md). **Changed the same day:** Adam signs in with an email and a password (no email service, no template edit, no code); the emailed code and its custom sender move to the beta's setup. The "Auth email template" step below no longer applies.

### What Adam does (about 15 minutes; a new runbook `docs/runbooks/phone-app.md` holds the steps)

1. **Tell Claude prod's Supabase project URL** (it isn't secret, and it stays out of the repo). Claude checks `https://<project>.supabase.co/auth/v1/.well-known/jwks.json`. **If `keys` is empty**, the project still signs tokens with the old shared secret: in the dashboard's JWT keys settings, move to an asymmetric signing key (ES256, the one Supabase recommends) and rotate to it. Prod has no sessions yet, so nothing is signed out.
2. **Auth email template:** in the Magic Link template, show `{{ .Token }}` (the 6-digit code) and drop the link. Leave the code's lifetime at an hour or less.
3. **Create his own sign-in:** Authentication → Users → add a user with the email his Supabase account uses (the built-in sender only delivers to organization members, 2 emails an hour), confirmed, with a long random password he never uses.
4. **Three values** at the hidden prompt (`scripts/gcp-secrets.sh`), before the PR merges: prod's **transaction pooler** URL (port 6543), from Bitwarden, as `fh-api-database-url`; the project URL as `fh-supabase-url`; and the placeholder `https://not-set.invalid` as `fh-app-origins` (Phase 3 replaces it with the app's address).
5. **Confirm sign-ups are off.** Claude's smoke script proves it: asking for a code for an unknown email must be refused.

### Decisions (write these up as ADR 0029 in this PR)

- **The API checks each token itself, against Supabase's public keys.** It fetches the JWKS once and caches it (10 minutes), then verifies signature, issuer (`<project URL>/auth/v1`), audience (`authenticated`; confirm both on the first real token, in the smoke script) and expiry on every request. Allowed algorithms are listed (`ES256`, `RS256`); anything else, `HS256` and `none` included, is refused. *Why:* no round trip to Supabase per request (ADR 0020), and no shared secret in our service that could forge any user. *Rejected:* the shared secret; asking Supabase's `/user` on every call (slower, and their outage becomes ours); letting the app read Postgres through Supabase's Data API (ADR 0008).
- **A sign-in is tied to our user by its id, not its email.** Migration 0007 adds `users.auth_user_id` (nullable, unique). The first time a token arrives whose email matches a user with no `auth_user_id`, the API sets it; after that only the id is looked at. No match: 403. The API never creates a user. *Why:* emails change, ids don't, and the same code is what M7's invites need. Only fields Supabase sets are trusted (`sub`, `email`, `role`); never `user_metadata`, which the user can edit.
- **The service is the same image, started with `fh serve`** (a new command that runs uvicorn on `$PORT`). One Dockerfile, one build, and `container.yml` can start it.
- **Its own account, `fh-api`, with the least it needs:** its database URL and the Supabase URL. **No encryption key, no R2, no ping key.** A test fails if a change gives it the key, like the one for the snapshot jobs.
- **Public at Cloud Run, private in the app.** The phone can't present Google credentials, so the service accepts unauthenticated requests at the platform (the owner grants that once, in `gcp-setup.sh`) and every route but `/livez` requires a valid token. At most 2 instances, scale to zero.
- **CORS is an allow-list** read from `FH_APP_ORIGINS`. Until Phase 3 knows the app's address, the secret holds a placeholder that matches nothing (`https://not-set.invalid`), because a secret with no value can't be mounted. Tokens travel in the `Authorization` header, never cookies.
- **The schema is committed.** `fh openapi` prints it, `web/openapi.json` holds it, and a test fails when they differ, the same idea as `alembic check`.

### Build, in this order

1. **Dependencies:** `fastapi`, `uvicorn`, `pyjwt[crypto]`. Check each one's current API before writing against it.
2. **Migration 0007** (`--rev-id 0007`): `users.auth_user_id`; table `push_subscriptions` (`id`, `user_id` → users, cascade; `endpoint` unique; `p256dh`; `auth`; `user_agent`; `created_at`; `last_success_at`), row-level security on. Additive only (ADR 0027).
3. **Repositories:** `users.get_by_auth_id`, `users.link_auth_id` (one `UPDATE ... WHERE auth_user_id IS NULL AND email = :email RETURNING`, with the token's email lower-cased the way `users.create` stores it); `push_subscriptions.upsert | delete | list_for_user`. Every function takes `user_id`.
4. **Settings:** `FH_SUPABASE_URL`, `FH_APP_ORIGINS`, and (used in Phase 4) `FH_VAPID_PUBLIC_KEY`. Alias only, like the rest.
5. **`api/`:** `app.py` (`create_app(settings)`, one pooled engine per process, pool size 2), `auth.py` (the verifier and the `current_user` dependency), `schemas.py`, `routes/health.py | me.py | reports.py | push.py`. Plain `def` handlers on the existing sync repositories.

   | Route | Returns |
   |---|---|
   | `GET /livez` | `{"status": "ok", "version": ...}`. No token, no database |
   | `GET /v1/me` | The user's name, time zone and leagues (id, name, platform) |
   | `GET /v1/reports/{daily\|weekly}?date=` | `{for_date, reports: [ReportDocument]}`, the latest date by default (`reports.for_user`, one query). Empty list when there are none, not a 404 |
   | `GET /v1/push/public-key` | The VAPID public key; 503 until Phase 4 sets it |
   | `PUT /v1/push/subscriptions`, `DELETE /v1/push/subscriptions` | Save or remove this device's subscription, keyed by its endpoint |

6. **CLI:** `fh serve`, `fh openapi`.
7. **Cloud side:** in `scripts/gcp-setup.sh`: the `fh-api` account; secrets `fh-api-database-url`, `fh-supabase-url`, `fh-app-origins`; a placeholder service `api`; the deployer's role on that service and on `fh-api`; public access. In `scripts/gcp-secrets.sh`: the new names, and refuse a port-5432 URL for `fh-api-database-url` (the mirror of the existing check). In `deploy.yml`: deploy `api` **after** `migrate`, with `--min-instances 0 --max-instances 2`, 512 MiB, a 30-second timeout.
8. **Tests:** the verifier against keys made in the test (good token; expired; wrong audience; wrong issuer; `HS256`; `none`; unknown key id). First sign-in links, second doesn't relink, unknown email is 403. **Two users in one database: A's token never returns B's reports or subscriptions** (the tenancy test M7 promises, started now). Routes through FastAPI's test client on Postgres. Workflow tests: the API's secrets, no encryption key, migrate before the service. `container.yml`: start the image with `serve` and fetch `/livez`.
9. **`scripts/api-smoke.sh`:** signs in through Supabase with a code Adam types at a hidden prompt, calls each route, and prints statuses and counts only. The token never reaches the terminal or the chat.
10. **Docs:** ADR 0029, `runbooks/phone-app.md`, accounts-and-secrets, architecture §9, the handoff.

### Traps

- **`gcloud run deploy` may want a project-level permission** the deployer doesn't have. ADR 0025 hit the same question for jobs and the job-level role was enough. If the first deploy fails with PERMISSION_DENIED, grant the narrowest role that fixes it and record it in the ADR; don't reach for `run.admin` on the project.
- **Public access is set by the owner, once.** Don't pass `--allow-unauthenticated` in `deploy.yml`: the deployer can't change IAM policy, and shouldn't.
- **The session pooler and the transaction pooler are different secrets on purpose** (ADR 0027). psycopg already never prepares statements, which the transaction pooler needs.
- **Don't build a "dev user" switch that skips the token check.** Tests override the dependency instead. A bypass flag is the kind of thing that ends up on in prod.
- **Supabase is retiring the old `anon` and `service_role` keys at the end of 2026.** The app uses the publishable key (`sb_publishable_...`). The API uses neither.

**Done when:** the smoke script shows 200s with Adam's token and his real reports' counts, 401 without a token, and "sign-ups refused" for an unknown email; CI and Container are green; the PR's review threads are resolved.

---

## Phase 3 (part 4): the phone app

**Goal:** Adam opens the app from his Home Screen, signs in with his email and password, and reads this morning's Today and This Week. Branch `m3-part4-pwa`.

### Decisions for Adam before this phase deploys

1. **Where the app lives: decided 2026-11-16 (Adam): Cloudflare Workers static assets, not Pages.** Cloudflare's own docs say to start new projects on Workers, which serves a static app the same way, for free, and is where their new work goes. Recorded as an Update to ADR 0009. Pages still works and is the fallback if anything about Workers gets in the way.
2. **A domain: decided 2026-11-16 (Adam): not now, `workers.dev` for the first deploy.** Why it matters: an installed web app and its notification permission belong to its address. Moving from `something.workers.dev` to a real domain later means every user reinstalls the app and turns notifications on again, and M7's friend invites need a domain anyway (for the email sender). **If a domain is coming before the beta, buy it before this phase's first deploy** and put the app on `app.<domain>` from day one (Workers needs the domain's DNS on Cloudflare). If not, `workers.dev` is fine for Adam alone.

### What Adam does (about 15 minutes)

- Nothing to install: Cloudflare builds the app. (Node is pinned to 24 in `web/.node-version`; the laptop's v23.5.0 is too old to run the app's tests, so anything run locally uses a standalone Node 24, **never Homebrew**, handoff §6.)
- In Cloudflare: create the Worker from the GitHub repo (authorize Cloudflare's GitHub app for this one repository), root directory `web`, build command `npm ci && npm run build`, deploy command `npx wrangler deploy`, branch `main`. Add three build variables from Bitwarden: `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`. None is secret (the app ships them), but they stay out of this repo.
- Add the app's address as `fh-app-origins` (`scripts/gcp-secrets.sh`), then run the Deploy workflow so the API allows it.
- On the iPhone: open the address in **Safari**, Share → Add to Home Screen, open it from the icon, sign in with his email and password **inside the Home Screen app** (it has its own storage).

The steps, in order and exact, are [runbooks/phone-app.md](runbooks/phone-app.md) Part 2.

### Build

1. **`web/`:** Vite, React, TypeScript, `vite-plugin-pwa` in `injectManifest` mode (Phase 4 needs a hand-written service worker for push). `@supabase/supabase-js` for sign-in only. `openapi-typescript` and `openapi-fetch` for a typed client generated from `web/openapi.json`. Pin Node for CI and Cloudflare with `web/.node-version`.
2. **Sign-in: email and password** (`signInWithPassword`; decided 2026-11-16, Adam), so no email service is needed. **The 6-digit code stays in the code, behind the build variable `VITE_SIGN_IN_WITH_CODE=true`** (off by default; it shows as "Email me a code instead"): `signInWithOtp({ email, options: { shouldCreateUser: false } })`, then `verifyOtp({ email, token, type: 'email' })`, with `inputmode="numeric"` and `autocomplete="one-time-code"`. The beta switches it on once it has a custom email sender and a domain. No magic link anywhere: a tapped link opens Safari, not the installed app (decided 2026-11-06). The app never creates a user.
3. **Two tabs.** Today (`/v1/reports/daily`) and This Week (`/v1/reports/weekly`), one section per league, one card per decision: title, body, and the reason on its own line. **A card kind the app doesn't know renders as a plain card**, so the API can add kinds without breaking installed apps.
4. **Honest about freshness.** Each report shows its date and when it was built. If the latest report isn't from today (in the user's time zone, from `/v1/me`), a banner says so. A stale report that looks current is the failure this product can't have.
5. **Install help:** on iOS Safari outside the installed app, show how to add it to the Home Screen. Manifest with `display: standalone`, icons at 192 and 512 plus a 180 px `apple-touch-icon`, safe-area padding for the tab bar.
6. **The service worker** precaches the app shell only. Requests to the API always go to the network; nothing signed-in is cached. `sw.js` is served with `Cache-Control: no-cache` so updates land on the next open.
7. **`web/wrangler.jsonc`:** `assets.directory: "./dist"`, `assets.not_found_handling: "single-page-application"`. Security headers from `web/headers.template`, written into `dist/_headers` at build with the exact API and Supabase addresses from the build variables (a content-security policy that allows those two and nothing else; no address lives in the repo). **Checked 2026-11-16:** static assets do honor `_headers` (run with `wrangler dev`: the headers arrive on `/`, the fallback, assets and icons, and `sw.js` is `no-cache`).
8. **CI:** `.github/workflows/web.yml` on `web/**`: install, typecheck, lint, unit tests (card rendering, the unknown-kind fallback, the stale banner, sign-in states), build, and regenerate the types to prove `openapi.json` is current. Not a required check, like `container.yml`.
9. **Dependabot:** add the `npm` ecosystem for `/web`, grouped and with the cooldown, and **hold its PRs for a person** (add `^web/` to the held paths in `dependabot-auto-merge.yml`): this code runs with a signed-in session on a phone, and Cloudflare builds it straight from `main`. Record it as an Update to ADR 0024.
10. **Docs:** ADR 0009 Update, `runbooks/phone-app.md` (install, sign-in, "the app shows yesterday"), repo-structure, the handoff.

### Traps

- **The app and the API each need the other's address.** Order: Phase 2 deploys the API with no allowed origins; this phase deploys the app; then the origin goes into `fh-app-origins` and the API is redeployed. The app's three build variables need the API's address first, so Part 2's step 1 comes before the Cloudflare step.
- **Cloudflare's npm 10 can't install the app's test tools** (an arborist crash on `vitest`). `web/package.json` pins `packageManager: npm@11.x`, which Workers Builds honors; if a build dies on `npm ci`, read which npm it used.
- **The footer's build id is a hash of the app's sources** (`web/build-id.ts`), not the commit, so Cloudflare's rebuild of every push to `main` doesn't make the service worker look new.
- **Service workers need HTTPS**, so the installed app can't be tested from the laptop over Wi-Fi. Test in a desktop browser with `npm run dev`, and on the phone at the deployed address. Each merge to `main` deploys.
- **A broken service worker is sticky.** Keep it small, test an update (deploy twice, see the second version arrive), and never cache the HTML shell with a long lifetime.
- **Cut line if time is short** (the roadmap's first lever): polish. Plain cards, system font, no animation, no offline copy of the last report. Don't cut the stale banner or the unknown-kind fallback.

**Done when:** on the iPhone, from the Home Screen icon, after a password sign-in, both tabs show the reports the 06:17 chain built that morning; a browser that isn't signed in gets only the sign-in screen; and the API still refuses a token that isn't Adam's (Phase 2's smoke check). With the emailed code switched on later, an email that isn't a user is refused a code.

---

## Phase 4 (part 5a): push

**Goal:** the phone says "Today's report is ready" a few minutes after 06:17, with the laptop closed, and a tap opens Today. Branch `m3-part5-push`.

### Decided by Adam (2026-11-22)

- **How often the weekly overview notifies.** It's rebuilt every morning (ADR 0026), and two notifications a minute apart every day is noise. **The daily report notifies every morning; the weekly overview notifies on Mondays only** and updates quietly on other days. One constant, `WEEKLY_NOTIFY_WEEKDAY` in `pipeline/notify.py`, and it is his to change.
- **Where the app is hosted:** Cloudflare Workers at a `workers.dev` address, which is never written in the repo (it lives in the secret `fh-app-origins`). The push sender takes its VAPID subject from that same secret, mounted on the report jobs (ADR 0030).

### Decisions (ADR 0030 in this PR)

- **The report jobs send the push, not the API.** `daily` (and `weekly`) send after their reports are committed. The VAPID private key is readable by `fh-reports` only; the API holds only the public key. *Why:* the job is the thing that knows a report is ready, and the internet-facing service shouldn't hold the key that can notify every user.
- **After commit, once.** A push announces a report that is saved, never one that could still roll back. Migration 0008 adds `reports.notified_at`; a rerun the same day finds it set and stays quiet.
- **Push never fails a run.** The report is built either way. A subscription that answers 404 or 410 is deleted; any other failure ends the run with a warning ("report built, push failed"), which is an ops event.
- **Every push shows a notification.** Safari takes the permission away from a site whose push arrives and shows nothing.
- **The VAPID subject is the first address in `fh-app-origins`**, mounted on the report jobs as `FH_APP_ORIGINS`: no second secret to drift, and no address or email in the repo.

### Build

1. **Keys:** `scripts/vapid-setup.sh` makes a P-256 key pair inside one process and adds the private key to `fh-vapid-private-key` and the public key to `fh-vapid-public-key`. It prints only the public key, and refuses to run again without `--rotate` (new keys orphan every subscription).
2. **Sender:** `delivery/push.py` with `pywebpush` (2.5.0 on 2026-11-15; read its current signature). A `PushSender` protocol so tests use a fake. Payload: a title, a short body built from counts ("8 decisions in 1 league"), a path (`/today`), a tag (`daily-2026-11-20`). Under 4 KB. TTL 6 hours. The VAPID subject is the app's own address (it must be a URL or a `mailto:`; Apple refuses anything else).
3. **Wiring:** in `cli/run.py`, after `run_to_completion` returns (the transaction is committed) and not on `--dry-run`. `finish_run` gets the push outcome as a possible warning.
4. **App:** an "Enable notifications" button. In its tap handler, and nowhere else: `Notification.requestPermission()`, `pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })`, `PUT /v1/push/subscriptions`. A matching "turn off". Hidden, with a hint, when the app isn't running from the Home Screen.
5. **Service worker:** `push` → always `showNotification`; `notificationclick` → focus the app or open the path.
6. **`fh push test`:** one test notification to the owner's devices, run as the `daily` job with its command replaced: `gcloud run jobs execute daily --region us-east4 --args=push,test --wait`. It sends and exits; it doesn't hand over to `weekly`.
7. **Cloud side:** the two secrets, `fh-reports` reads the private one, `fh-api` the public one; `deploy.yml` mounts them; workflow tests pin who gets which.
8. **Tests:** sent once per user, kind and date; quiet on a rerun; 410 deletes the row; a 403 warns and the run still exits 0; no subscriptions means no push and no warning; never on a dry run; Monday-only for weekly.

### Found while building (2026-11-22)

- **`pywebpush` has traps, each pinned by a test:** it changes the claims dictionary it is given (a fresh one per send), it has no timeout unless passed one, its errors are its own exception or a bare `requests` one, and the private key must be the raw 32-byte value in URL-safe base64.
- **A subscription is tied to the public key it was made with.** After `vapid-setup.sh --rotate` every device's subscription is orphaned, so the app compares the subscription's key with the server's and shows "off" on a mismatch; turning it on replaces the old one (and the server forgets the old address).
- **`PushSubscription.toJSON()` has an `expirationTime` the API refuses** (its schema rejects extra fields), so the app sends only the address and the two keys.
- **The review found the jobs would send to any https address a user saved (fixed 2026-11-23).** The API now saves, and the sender sends to, only Apple's, Google's, Mozilla's and Microsoft's push hosts (one list in `domain/push.py`); redirects are never followed; each send has 8 seconds by the clock and the whole push 30, because the 5-second HTTP timeout is per read and a trickling server could outlast it.
- **A refused push leaves the report unannounced and the device kept**, so a rerun after the fix announces it; only 404 and 410 delete a device.
- **Only the report jobs can read the private key:** it is mounted on `daily` and `weekly` only (not the snapshot jobs, `migrate` or the API), and a workflow test pins every secret's readers.

### Traps

- **iOS shows the permission prompt only for a tap, inside the installed app.** A request on page load does nothing, silently.
- **Send after commit.** Inside `run_daily` the transaction is still open.
- **The private key's format** has to be one `pywebpush` accepts from a string, since it arrives as an environment value, not a file.

**Done when:** a scheduled morning, laptop closed, puts the notification on the iPhone within about ten minutes of 06:17; the tap opens Today; a second run that day doesn't notify again; `fh push test` works.

---

## Phase 5 (part 5b): backups, locks, loose ends, the exit test

**Goal:** the registry has a copy outside Supabase, the history can't be deleted with a leaked key, and M3's three exit tests pass. Branch `m3-part5-ops`.

### Decision for Adam

- **Backups get their own bucket and their own key.** Today the snapshot jobs and the report jobs share one R2 token. Backups hold users, league rows, the registry and credential ciphertext; the job that reads the public internet shouldn't be able to read or delete them. **Recommended:** bucket `fantasy-helper-backups`, its own token, a lock rule that forbids deleting or overwriting anything for 30 days, and a lifecycle rule that removes backups after 35. About ten minutes in Cloudflare.

### Decisions (ADR 0031 in this PR, extending ADR 0021)

- **A backup is one gzipped JSON-lines file per table, plus a manifest** (row counts, a checksum per file, the migration revision, the app version), under `<date>/<time>/`. Every table is read inside one `REPEATABLE READ` transaction, so the files agree with each other even while a run is writing. *Why:* about a hundred lines of code we can test, readable with any tool, and no Postgres client in the image that has to match Supabase's server version. *Rejected:* `pg_dump` (a version-matched binary in the image, and Supabase's own roles and extensions in every dump); Supabase's paid backups ($25 a month).
- **Every table is classified in code as backed up or rebuildable, and a test fails on a table that's neither.** Backed up now: `users`, `leagues`, `platform_credentials`, `recommendations`, `fact_sheets`, `reports`, `push_subscriptions`, `runs`. Everything else rebuilds from raw with `fh ingest`. Takes, grades and feedback join the list when they exist. When unsure about a table, back it up: a small table costs nothing.
- **On its own clock, not on the chain:** a `backup` job at 09:17 Eastern (after the morning's calls are logged), started by Cloud Scheduler (the third entry, still inside the free tier), watched by Healthchecks, running as its own account `fh-backup`. A backup shouldn't depend on the pipeline it protects having worked that day.
- **A restore drill is part of done.** `scripts/backup-drill.sh` starts a throwaway Postgres (handoff §6), migrates it, loads the newest backup, and compares counts and checksums with the manifest. `fh backup restore` refuses a database that isn't empty or whose name has neither `scratch` nor `test` in it.

### Build

1. `storage/backup.py` (export and restore, typed both ways), `fh backup run | verify | restore`, the table classification and its test, a round-trip integration test.
2. The `backup` job: `jobs.conf`, `deploy.yml`, `gcp-setup.sh` (account, two secrets for the backup bucket's key), `healthchecks-setup.sh` picks it up.
3. **Bucket lock on the raw bucket's public prefixes** (Adam, in Cloudflare; indefinite): `espn/season/`, `espn/players_projections/`, `espn/game_logs/`, `espn/pro_schedule/`, `espn/athletes/`, `sleeper/state/`, `sleeper/players/`, `sleeper/projections_season/`, `sleeper/projections_week/`, `fh/snapshot_complete/`. **Never `espn/leagues/` or `sleeper/leagues/`:** a friend who leaves must be deletable (ADR 0023). A rule on `espn/` would cover both, so it's one rule per dataset. Check the list against `pipeline/snapshot.py` first.
4. **The items tabled from PR #4's review:** 8 and 9 (a reused once-a-day result) are partly answered by ADR 0028, which reads a reused file's saved validity; re-read both against `pipeline/morning.py`, then close them or fix what's left. 6, 7, 10 and 12 stay tabled unless `raw_upload.py` or the R2 store is touched.
5. **Database size:** the 60% and 80% warnings already exist (`pipeline/ingest.py`). Confirm one appears in a daily run's summary by lowering the threshold in a test, not in prod.
6. `runbooks/backups.md`; handoff, roadmap, accounts-and-secrets, architecture §5.5.

### The exit test (M3's "done when")

1. **Laptop closed.** After Phase 1, shut the laptop the night before. In the morning: the notification, both tabs current, four green checks in Healthchecks.
2. **A deliberately broken run.** This one breaks nothing real: it points one `weekly` run at an empty raw store, so its freshness gate finds no marker, retries twice, and fails before touching the database.

```bash
gcloud run jobs execute weekly --region us-east4 --update-env-vars=FH_RAW_STORE=local --wait
```

   Expect, over about 8 minutes: two "retrying" notes, a loud `Weekly run failed` on the prod topic ("this morning's snapshot isn't saved yet, after 3 attempts"), a failed execution, a failed row in the run log, and Healthchecks' `weekly` check down. Then start `weekly` by hand (`gcloud run jobs execute weekly --region us-east4 --wait`) and watch the check come back up.

3. **A backup restores.** `scripts/backup-drill.sh` prints matching counts.

**Done when:** all three pass on the same day, the status table above says so, and the roadmap marks M3 done.

---

## After M3

- **Chat comes early (Adam, 2026-11-15).** It is M4b in the roadmap, about Nov 25, a third lane. From this plan it needs: the API's pattern (a signed-in route; Phase 2), the app's Chat tab (Phase 3), and the Claude and LangSmith keys in the cloud with a spend cap (below). Nothing in Phases 2 to 5 needs to change for it, but **keep the app's card rendering generic** (Phase 3, item 3): it is also what lets reports be customized per person later.
The dates in roadmap v0.5 assumed M3 would end on 2026-11-12. It won't end before about 2026-11-23, so everything after it moves. Roadmap v0.6 has the new dates; they are a proposal until Adam confirms them.

- **A smarter weekly planner, and a buddy that knows how leagues work (Adam, 2026-11-16).** Planning the week day by day (when to make each add, injured players' returns, the add limit as a budget) and teaching the chat the leagues' mechanics. It is engine and facts work with no files shared with the app or the API, so it can run as a lane beside Phases 3 to 5. It is **not** one of M3's phases. See the roadmap's direction of that name.
- **M4 can start beside Phase 3.** Its lane A (the LLM gateway, the baseline graph, guardrails, the MCP server) lives in `brain/` and `mcp_server/` and touches nothing the phone app does. Running it as a second session is the difference between a beta around 2026-12-06 and one around 2026-12-12; the hard stop is 2026-12-15.
- **Before M4 writes its first model call,** the cloud jobs need two secrets they don't have: the Claude key (`fh-anthropic-api-key`, with a spend limit set in the Console) and the LangSmith key (rule 3: every model call is traced, and today only CI and the laptop have that key). Both go into Secret Manager for `fh-reports`, not into GitHub's `prod` as the older setup runbook says.
- **Before M7:** a domain (see Phase 3), a custom email sender for Supabase Auth, the tenancy test extended to every new route, and a database role for the API that can't touch tables it doesn't serve (today it connects as the owner, like the jobs; fine for one user, worth narrowing before friends' logins are in the database).

## Decisions waiting on Adam

| # | Decision | Recommended | Needed by |
|---|---|---|---|
| 1 | The new dates, and whether M4's lane A runs in parallel | Parallel, beta about 2026-12-06 | Now |
| 2 | Workers static assets instead of Pages | **Workers. Decided 2026-11-16 (Adam)** | Phase 3 |
| 3 | A domain before the app's first deploy | **Not now (Adam, 2026-11-16): use `workers.dev` for now.** Buy one before the beta (friends' sign-in emails need it); until then, moving means reinstalling the app and turning notifications on again | Phase 3, again before M7 |
| 4 | Weekly overview notifies on Mondays only | Yes | Phase 4 |
| 5 | A separate, locked bucket for backups | Yes | Phase 5 |
| 6 | Google Cloud's project ID and number show in the public deploy logs (GitHub prints environment variables; the repo itself doesn't hold them) | Accept: they identify, they don't grant, and deploys are tied to this repo, branch and workflow. To hide them anyway, store them as `prod` secrets instead of variables | Any time |
| 7 | Where the smarter weekly planner goes (roadmap, "Direction: a smarter weekly planner") | **Decided 2026-11-16 (Adam): start now.** The smallest piece is built (PR 21: the report says who each day's count left out and why); the day-by-day planning stays beside M4 | Done for the first piece |

## Decisions waiting on Adam, added 2026-11-16

| # | Decision | Recommended | Needed by |
|---|---|---|---|
| 8 | Where the daily "what happened" cards go (roadmap, "Direction: a daily report that says what happened in each league") | Engine-only cards (league standouts, pickups, his team against projections, his day's points against his opponent's) as a lane beside Phases 3 to 5, before M4's brain | **Decided 2026-11-16 (Adam): yes, as a lane beside Phase 2.** Built the same day: every league's daily report has the cards, checked against the platform's own daily totals (ADR 0033) |
| 9 | Move the one-tap "I locked / I passed" button from M5 to right after the app's first deploy | Yes: without it, a lock card can recommend a player already locked | Before Phase 4 |

## Outside facts (checked 2026-11-15)

Read from each vendor's own documentation on that date. Re-read the one a phase leans on before building.

| Fact | Where | Used in |
|---|---|---|
| Supabase publishes a project's public signing keys at `/auth/v1/.well-known/jwks.json`; the list is empty unless the project uses asymmetric keys; ES256 is recommended; the endpoint is cached for 10 minutes | supabase.com/docs/guides/auth/signing-keys, /auth/jwts | Phase 2 |
| Email codes: `signInWithOtp` with `shouldCreateUser: false`, `verifyOtp` with `type: 'email'`, `{{ .Token }}` in the Magic Link template, codes last an hour by default, one request per 60 seconds | supabase.com/docs/guides/auth/auth-email-passwordless | Phases 2, 3 |
| The built-in email sender reaches only the organization's members, 2 an hour, and isn't for production | supabase.com/docs/guides/auth/auth-smtp | Phase 2, M7 |
| The publishable key (`sb_publishable_...`) is the one for apps; the old `anon` and `service_role` keys are being retired by the end of 2026 | supabase.com/docs/guides/api/api-keys | Phase 3 |
| Cloudflare: "Start new projects with Workers"; static assets are served free; `not_found_handling: "single-page-application"`; Workers Builds has a root directory and build variables; custom domains need Cloudflare DNS | developers.cloudflare.com/pages, /workers/static-assets, /workers/ci-cd/builds | Phase 3 |
| Workers Builds (re-read 2026-11-16): the GitHub app can be limited to "Only select repositories"; the Worker's name in the dashboard must match `name` in the wrangler config; every push to the production branch builds unless **Build watch paths** say otherwise; other branches build by default (previews); build variables are set under Settings → Build; the build reads `.node-version` and the `packageManager` field | developers.cloudflare.com/workers/ci-cd/builds (git-integration, build-watch-paths, configuration) | Phase 3 |
| Static assets honor a `_headers` file, and `wrangler deploy --dry-run` needs no login (so CI can prove the Worker config offline) | verified by running `wrangler dev` and `--dry-run` locally, 2026-11-16 | Phase 3 |
| Cloud Run: a public service is made by turning off the invoker check or by granting `allUsers` the invoker role | docs.cloud.google.com/run/docs/authenticating/public | Phase 2 |
| iOS web push: Home Screen web apps only (16.4+); the prompt needs a tap; every push must show a notification or the permission is revoked; the VAPID subject must be a URL or `mailto:`; payloads up to 4 KB; 410 means the subscription is gone | developer.apple.com, "Sending web push notifications in web apps and browsers" | Phase 4 |
| `pywebpush` 2.5.0 (2026-08-29), Python 3.10+, maintained | pypi.org/project/pywebpush | Phase 4 |
| R2 bucket locks: per prefix, by age, date or indefinitely, up to 1,000 rules, and they outrank lifecycle rules; changing them needs a token that can edit bucket settings, which ours can't | developers.cloudflare.com/r2/buckets/bucket-locks | Phase 5 |
