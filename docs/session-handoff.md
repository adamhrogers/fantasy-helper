# Session handoff: everything a new session needs

**Last updated:** 2026-11-16, during M3. Parts 1 and 2 are merged (PRs #4, #12, #14, #16, #17) and part 2's rollout is done: the morning chain ran on its own at 06:17 on 2026-11-16. Part 3's code is written on branch `m3-part3-api` (no PR); parts 4 and 5 are planned, not started. **Now: [docs/m3-plan.md](m3-plan.md)**, the phased plan for the rest of M3. Phase 1 (end the snapshot overlap) is done except three steps on Adam's laptop (Cloud Run has been the only timer since 2026-11-16); next is Phase 2.
**Read this first** (and [plain-english-guide.md](plain-english-guide.md) if you want the whole project explained without jargon), then `docs/m3-plan.md` (what to do next, in order), `CLAUDE.md` (rules), `docs/roadmap.md` (milestones), `docs/architecture.md` (design) and `docs/decisions/` (ADRs). This file is the narrative glue: what exists, how we work, what we learned the hard way, and exactly what comes next.

The repo is **public**, so this file holds no secrets or personal data. League names and IDs, the owner's email, the Supabase project ID and the ntfy topic live in `.env` and the database. `uv run fh league list` shows the leagues.

---

## 1. The project in one paragraph

A fantasy basketball assistant for Adam (customer one), then a friends beta, then a paid launch for 2027-28. Every morning it sends a short briefing to his phone, with specific moves tuned to each league's own scoring, built on his own takes (a watchlist of beliefs), and it **grades every call it makes** against a baseline, all season. It doubles as a portfolio case study (deployment strategist; "the Blitzy pattern scaled down": an orchestrator with specialized agents and validation). The source spec is *Fantasy Basketball Helper: Product Spec* (2026-10-15, 25 pages; it lives outside the repo).

**Core principles** (never broken; details in CLAUDE.md):
- **Code computes every number; models only argue.** The engine makes candidate moves with expected values; LLMs read a FactSheet, select, explain and challenge; guardrails reject ungrounded numbers.
- **Evidence over opinion.** Every call is logged before tip-off and never edited; inputs are versioned snapshots; grades come later, against a baseline.
- **Adapters isolate platforms.** ESPN's API is unofficial; when it changes, only `adapters/espn/` changes.

**The long-term goal (Adam, 2026-11-15):** the daily and weekly reports are the first deliverable, not the finish line. The main thing he wants is **a chat buddy he can talk to all the time**, about players, moves and his own opinions, that remembers what he tells it. The data we collect and his opinions are what it works from, and the grading loop keeps it honest. He also asked (2026-11-15) for the chat **as early as it logically fits**, so it is phased as **M4b** (about Nov 25, a third lane). And he wants reports **customizable per user** as a direction, not now: keep the app drawing reports generically from their cards. See "The buddy" and "Direction: reports shaped by each user" in `docs/roadmap.md`; the foundations are in the design, the memory design and exact dates are proposals waiting on his OK. Don't build chat before M4's tool server and brain exist, and don't let it write to the call log: opinions become confirmed, gradable takes.

## 2. Where things stand (2026-11-15)

| Milestone | Status |
|---|---|
| M0 Foundation | **Done, merged** (PR #1) |
| M1 Data spike | **Done, merged** (PR #2) |
| Pre-M2 review | **Done**: latency (ADR 0020), storage budget and season archive (ADR 0021), lock-in moved to M2 |
| M2 Scoring, weekly overview, **Sleeper lock-in** | **Done, merged** (PR #3). Built:
- box scores: `espn.game_logs`, migration 0003, backfilled from opening night (6,935 rows)
- typed projection columns and read-path indexes (dev database at 0003)
- **one box score for every league** (ADR 0022, CLAUDE.md rule 9): complete for every game played, internally consistent, and every league's settings mapped onto it, missed shots and fouls included
- `engine/scoring.py` with golden and property tests, verified against ESPN (155/155) and Sleeper (417/417 on Sleeper's own stats; 405/417 on ESPN's, the 12 from one-stat feed differences): [spikes/m2-scoring-check.md](spikes/m2-scoring-check.md)

- **lock-in v1 built (uncommitted at first):** `engine/distributions.py`, `engine/lock_in.py`, `facts/lock_in.py`, the append-only `recommendations` table (migration 0004, a trigger refuses UPDATE and DELETE; dev is at 0004), `fh run daily [--dry-run] [--open]`, and the engine-only daily report as a local page. The first 8 calls were logged in dev on 2026-11-06 at 7:54 pm ET.
- **Locks:** Adam gave his real week-3 locks. Sleeper's weekly numbers fit no rule against them (they flip between pulls minutes apart), so cards show every open decision and lock detection is **parked** (Adam, 2026-11-06): the lock-or-pass recommendation is enough for now. A user-set lock flag on the roster stays an idea for later.

- **Weekly overview built:**
  - `engine/schedule.py` (daily lineups, most specific slot first)
  - `engine/valuation.py`, `engine/win_probability.py`, `engine/streaming.py` (greedy; only drops players below replacement on their season average) and `engine/baselines.py`
  - `facts/weekly.py`, `domain/facts.py` (FactSheet v1, about 1,400 tokens)
  - migration 0005: `fact_sheets` (append-only) and `league_player_values` (replaced each run); dev is at 0005
  - `reports/weekly.py`, `fh run weekly`
  - Moves and the whole-week matchup forecast are logged as calls.
- **Mid-week runs project the rest of the week only,** with no win probability, because realized points so far aren't counted yet.
- **Projections are blended with game logs:** the projection counts as 10 games of evidence, because RotoWire sometimes projects a bench player far above what he does.

- **Daily reconciliation built:** `pipeline/reconcile.py` checks box scores against each platform's own numbers every morning. First live run: ESPN 75/75, Sleeper 271/275.

- **Mid-week win probability built** for daily-lineup leagues:
  - ingest stores ESPN's lineup for each finished day of the week (`view=mRoster&scoringPeriodId`, recorded fixture `espn_points/lineups.json`);
  - banked points = each day's starters times our box scores (ADR 0022);
  - checked against ESPN's own daily totals (live: 773 vs 773).
  - Lock-in leagues still show "rest of the week" (locks invisible).

- **PR #3 open; review fixed (2026-11-06).** A `/code-review` posted 15 findings as inline comments on the PR; all are fixed, each reply names its commit. The ones that change behaviour:
  - players ruled out or suspended no longer fall back to their season projection;
  - games that tipped today count once they're over (banked in daily-lineup leagues, lockable in lock-in leagues);
  - streaming never drops a player without a season average yet;
  - one failed day of ESPN lineups no longer fails the league;
  - the daily check skips teams whose games we haven't loaded box scores for;
  - categories leagues store no value over replacement.
  - A matchup period that started before this week (the All-Star break) is flagged, with no banked points or win probability, until periods are mapped properly.

Left for later: see §7. |
| M3 Phone app + delivery | **In progress**, about ten days behind roadmap v0.5 (it was planned for Nov 7–12). **Part 1 merged and live:** cloud snapshots on R2 (PR #4, ADR 0023), moved to Cloud Run Jobs on 2026-11-14 because GitHub's scheduler ran hours late (PRs #12, #14, ADR 0025). **Part 2 merged** (PR #16, 2026-11-15, ADRs 0026–0028): reports saved, built in the cloud by a morning chain, prod migrated by the deploy; **its rollout is done** (2026-11-16: Adam's leagues and all history are in prod, and the chain ran on its own at 06:17). **Parts 3 to 5** (the API, the phone app, push, backups, the exit test): planned in [m3-plan.md](m3-plan.md). The API's code is written on branch `m3-part3-api` (pushed, no PR; held until its cloud setup is done); the rest is not started. **Dependabot merged** (PR #5, ADR 0024). Every account M3 needs exists ([runbooks/m3-account-setup.md](runbooks/m3-account-setup.md)); what exists where: [runbooks/accounts-and-secrets.md](runbooks/accounts-and-secrets.md). |
| M4–M8, P9–P12 | See `docs/roadmap.md` v0.6 (dates re-baselined 2026-11-15, **proposed, waiting on Adam**). Friends beta about **Dec 8**, which depends on running M4's brain work and the chat (M4b) as parallel lanes beside the phone app; hard stop **Dec 15**. |

### What runs today
- **`fh snapshot`** runs in the cloud: the **Cloud Run jobs** `snapshot-morning` and `snapshot-evening` (ADR 0025, live since 2026-11-14, on the minute at 06:17 and 17:17 Eastern, watched by Healthchecks; [runbooks/scheduled-jobs.md](runbooks/scheduled-jobs.md)) save raw ESPN and Sleeper public data to R2, and they are the only timer. **The laptop's launchd schedule and GitHub's late schedule were removed on 2026-11-16** (Adam's call, after 3 of the 4 planned on-time runs: 2026-11-15 06:17 and 17:17, and 2026-11-16 06:17; the fourth wasn't waited for). `scripts/snapshot-schedule.sh status` says `not installed` for both jobs, and `snapshot.yml` is a manual **Actions → Snapshot → Run workflow** fallback only. The laptop's own copies in `data/raw/` (git-ignored) run from 2026-11-06 to 2026-11-15; the ones through 2026-11-14 are in R2, and `fh raw upload` sends the rest. **Left for Adam:** run `fh raw upload`, give the laptop its own R2 key, and set `FH_RAW_STORE=r2` in `.env` ([runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md), section 5, steps 2 to 4). A missed snapshot can't be recovered (rule 8): Healthchecks alerts the phone when one doesn't start.
- **`fh ingest`** is run by hand for now. It loads every not-yet-loaded snapshot plus fresh league data into Supabase, takes about 30 seconds, and is idempotent. First live run: 1,100 ESPN players; **all 1,852 active Sleeper players** (the Sleeper league has 142 rostered, all linked to ESPN, and **1,710 free agents**, 889 linked to ESPN and 410 with Sleeper projections); 1,200 games; 9 projection snapshots (8,490 rows); 226 injury changes; full rosters, matchups and transactions for all three leagues.
- **`fh run daily` and `fh run weekly`** run by hand on the laptop against dev. **In the cloud they run every morning against prod** (by hand on the evening of 2026-11-15, on their own since 2026-11-16), as the second and third links of a chain: the 06:17 snapshot starts `daily` only when **every** dataset saved and passed, and `daily` starts `weekly` when it finishes (Cloud Run jobs; ADR 0026, 0028). Each runs ingest first, which also loads box scores and ESPN's lineup for each finished day, logs every call in the registry, and **saves its report** in the `reports` table. By hand they also write local HTML pages (`--open` opens them); `fh run daily --dry-run` saves nothing. A run with a failed league, or that can't finish, exits 3 (the phone was told); warnings alone exit 0. The scheduled jobs add `--require-snapshot-today`: no completion marker from today (written by the snapshot only when everything passed) means they wait, then fail. `fh report open [weekly]` shows the latest saved report; against prod, `scripts/fh-prod.sh report open`.
- **Supabase dev project**, migrated to **0006**. The **prod project** exists since 2026-11-07 and **is at 0006 too**: the deploy that followed PR #16 ran the `migrate` Cloud Run job and waited for it (2026-11-15; the job's log says `migrated an empty database to 0006`; ADR 0027). It holds Adam's user, his three leagues, all snapshot history since 2026-11-06 (loaded on 2026-11-15) and every call the cloud jobs have logged since. Its URL lives in Secret Manager (`fh-database-url`), reached from the laptop only through `scripts/fh-prod.sh`.
- **Google Cloud, as deployed on 2026-11-15:** five Cloud Run jobs on the merged image (`snapshot-morning`, `snapshot-evening`, `migrate`, `daily`, `weekly`), two Cloud Scheduler entries (the snapshots), seven secrets, and the chain's permissions in place (`fh-jobs` may start `daily`, `fh-reports` may start `weekly`). `scripts/gcp-setup.sh show` lists it all. The hand-over has run, by hand on 2026-11-15 and on its own at 06:17 on 2026-11-16.
- **LangSmith tracing** works (`fh doctor` sends a smoke trace). **ntfy run events** reach Adam's phone: every run sends started, steps and success quietly and failures loudly, and it's ops only; product notifications come from the PWA ([runbooks/run-events.md](runbooks/run-events.md), added 2026-11-06). iPhone notifications needed a settings fix, which is done.
- **GitHub:** `adamhrogers/fantasy-helper`, public. CI runs lint, mypy, contracts, tests on Postgres 17, `alembic check`, and a gitleaks full-history scan. The repository secret `LANGSMITH_API_KEY` is set, and CI's smoke trace passes on `main` (checked 2026-11-06). Since 2026-11-07: the `prod` environment (only `main`; all 8 secrets since 2026-11-14), the `main` ruleset (both CI jobs required, admin bypass), and Dependabot with auto-merge. Every action is pinned to a commit.

### Adam's three leagues (all 10 teams, season 2026-27 = `season 2027`)
| Fixture alias | Platform / format | Adam's team | Key rules |
|---|---|---|---|
| `espn_points` | ESPN, H2H points | team **13** | PTS +1, AST +2, REB +1, STL +4, BLK +4, TO −2, FGM +2, FGA −1, FTM +1, FTA −1, 3PM +1. Daily lineups (`INDIVIDUAL_GAME`). Adds: **0.571 a day** (4 per 7-day matchup). Slots: PG, SG, SF, PF, C, G, F, 3 UTIL, 4 BENCH, 2 IR. |
| `espn_categories` | ESPN, H2H most categories (9-cat) | team **12** | PTS, BLK, STL, AST, REB, TO (reversed), 3PM, FG%, FT%. Adds 1.0 a day. 3 BENCH, 3 IR. |
| `sleeper_points` | Sleeper, points, **lock-in** (`game_mode 1`, confirmed by Adam) | roster **1** | PTS +1, REB +1, AST **+2** (the spec said 4; the settings win), STL +3, BLK +3, TO −2, FGM +1, FGA −0.5, FTM +1, FTA −1, 3PM +0.5; **bonuses +2 at 40 points and +2 more at 50**. Slots: PG, SG, G, SF, PF, F, C, 3 UTIL, 3 BENCH, 2 IR. |

The spec said "friends beta not in Adam's competitive ESPN leagues; possibly the Sleeper league."

## 3. How we work with Adam

- **Plans first, explicit approval for anything big or outward-facing.** He reviews plans and says "go for it" before code. **Every commit, push, PR and installed background job was explicitly approved** ("Commit it", "Install it"). Ask with a short question and buttons; don't assume. Approval doesn't carry over to the next action.
- **One branch and one PR per milestone** (`m0-foundation`, `m1-data-spike`), merged into `main` **by Adam**. **M3 is the exception: one branch and PR per part** (`m3-part3-api`, …), because the deploy and `prod` work only from `main`. Commits within a branch are made at checkpoints he approves.
- **Commit footer:** `Co-Authored-By:` with the name of the model doing the work, as the session's own instructions give it (earlier commits say Claude Opus 5.5). **PR body footer:** `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. After opening a PR, use the app's PR tools (get_status / bind) and offer Auto-fix; Adam turned it on for PR #1 and PR #3.
- **Prod secrets are read only by Adam's own commands.** On 2026-11-15 the app's permission check refused `scripts/fh-prod.sh doctor` from Claude's session. Listing jobs, policies, run history and job logs with `gcloud` works and is how the cloud's state gets checked; anything that reads a secret's value is Adam's to run, or to allow.
- **Secrets never pass through chat.**
  - Keys are generated straight into `.env` without being printed.
  - ESPN cookies are typed at `fh league add`'s hidden prompt.
  - GitHub secrets are set by Adam with `gh secret set`.
  - Scripts that touch real league data print **structure and counts only**, never member names.
- **Style he likes:** plain language, concise, step-by-step for anything he has to do by hand, with commands in their own fenced `bash` blocks. He asks "what is X?" questions; answer them directly. He isn't attached to specific tools and accepts reasoned recommendations.
- **He wants the reasoning and the honest state:** what was verified, what wasn't, and false alarms called false alarms. He specifically asked for a leak audit before merging M0. It was clean: `.env` never committed, none of his secret values in history or the PR, gitleaks and GitHub secret scanning clean.
- **Code reviews go on GitHub** (Adam, PR #3): post the findings as inline review comments on the PR, fix them all, reply on each comment with the fixing commit and its test, then resolve the threads. He merges once the threads are resolved and CI is green.
- **Live data has context** (Adam, 2026-11-06): reason with dates and the matchup week as a whole; games in progress today aren't final. Don't fit a rule to data that isn't consistent; say it doesn't fit (that's how lock detection got parked).
- **Parallelism:** sub-agents and parallel sessions are fine once a plan is approved. M2 and M3 were the natural pair to split. The next one is M4's brain work (`brain/`, `mcp_server/`) beside M3's phone app (`web/`, `api/`, `delivery/`): they share no files (m3-plan.md, "After M3").

## 4. Architecture as built (quick map)

Layers, enforced by import-linter in CI: `cli` → `api | mcp_server` → `pipeline` → `evaluation` → `brain | reports | delivery` → `facts | registry` → `engine | identity` → `storage | adapters` → `observability` → `config` → `domain`. The engine is pure (no storage, adapters or I/O); the brain sees only FactSheets and MCP tools. **A new top-level package must be added to the contracts in `pyproject.toml`.**

| Area | What exists (M0 + M1; M2 additions in the next table) |
|---|---|
| `domain/` | Frozen Pydantic models, UTC-only (`UtcDatetime`). `league.py`: `LeagueRules` (hashed), `ScoringRules` with `bonuses`, ratio `CategoryRule`s (FG%), named `RosterSlot`s (`SLOT_POSITIONS`), `AcquisitionLimits.per_matchup_per_day`. `records.py`: **platform records keyed by each platform's own IDs** (`PlayerKey`, `SourcePlayer`, `RosterEntryRecord`, `MatchupRecord`, `TransactionRecord`, `ProjectionRecord`, `InjuryRecord`, `ScheduledGameRecord`, `RulesRecord`). Also recommendations (tip-off guard), takes, trades, evaluations (timeline points), reports (`ReportDocument`), runs, ports. |
| `adapters/` | `_http.py`: `get_raw` with retries, `secret_headers` (sent, never recorded) and list-valued params. `espn/public.py` (no login), `espn/league.py` (cookies), `espn/mapper.py`; the same three for `sleeper/`. `redaction.py`: the fixture redactor and leak checker. **Mappers are pure functions.** |
| `identity/` | `names.py` (normalization), `matching.py` (pure matcher: override → **name + birth date ±1 day (trade-proof)** → name + team → unique name; birth dates are evidence, never a veto), `crosswalk.py` (`sync_players`, `Resolver`; links are stored by platform IDs, so trades never break them; teams refresh every run), `overrides.py` (manual Sleeper → ESPN links; empty so far). ADR 0018. |
| `storage/` | SQLAlchemy 2.1, Alembic `0001` + `0002`, `crypto.py` (AES-GCM bound to the owner), `raw_store.py` (append-only), repositories: users, leagues, credentials, players, league_data, nba, runs. |
| `pipeline/` | `snapshot.py`, `onboarding.py` (`fh league add`), `fixtures.py` (`fh fixtures record`), `ingest.py`. |
| `observability/` | `logs.py` (structlog with secret redaction), `tracing.py` (LangSmith), `alerts.py` (ntfy). |
| `cli/` | `fh version | keygen | doctor | snapshot | league add/list | fixtures record | ingest | run daily | run weekly`. |
| Empty, waiting | `brain`, `delivery`, `evaluation`, `api`, `mcp_server` (docstring only). |

**Added in M2:**

| Area | What exists |
|---|---|
| `engine/` | Pure math: `scoring` (each league's weights and bonuses on the shared box score), `distributions` (projection blended with game logs, spread shrunk to a league prior), `lock_in` (backward induction, plus `week_value` for a week's lock-in value), `schedule` (daily lineups, most specific slot first), `valuation`, `win_probability`, `streaming`, `baselines`, `reconcile`. |
| `facts/` | `lock_in.py`, `weekly.py` (FactSheet v1: banked points, the rest of the week, adds left, `period_mismatch`), `availability.py` (out or suspended players get no season fallback). |
| `registry/` | `calls.log_call`: append-only, deduped, tip-off guard. |
| `reports/` | `daily.py` (lock-or-pass cards), `weekly.py`, `html.py` (local pages). |
| `pipeline/` | `daily.py`, `weekly.py`, `lock_calls.py`, `reconcile.py` (the daily check), `retry.py` (`run_with_retries`: retries only failures a retry can fix). |
| `observability/` | `events.py`: `RunEvents` (ops-only ntfy, [runbooks/run-events.md](runbooks/run-events.md)). |

**Added in M3 (parts 1 and 2):**

| Area | What exists |
|---|---|
| `storage/` | `r2_store.py` (`R2RawStore`: never overwrites, two guards), `migrate.py` (`fh migrate`), `repositories/reports.py` (save, replace on a rerun, latest for a user in one query), `repositories/database.py` (size, and the run lock every writing run takes). |
| `pipeline/` | `raw_upload.py`, `freshness.py` (the completion marker and `--require-snapshot-today`), `morning.py` (when a morning set counts as complete). |
| `adapters/` | `cloud_run.py`: one job starts the next, as its own service account (the morning chain, ADR 0028). |
| `observability/` | `healthchecks.py`: start, success and fail pings; a ping never raises. |
| `cli/` | `_common.py` (`watched`, `crash_guard`, `REPORTED_EXIT` = 3), `_runs.py` (retries, one ending per run, `start_next`), `report.py` (`fh report open`), `raw.py`, plus `fh migrate`. |
| Outside `src/` | `Dockerfile` (one image for every job), `.github/workflows/deploy.yml | container.yml | snapshot.yml`, `scripts/gcp-setup.sh | gcp-secrets.sh | fh-prod.sh | healthchecks-setup.sh`, and `scripts/jobs.conf`, the one table of jobs that Cloud Scheduler, the chain's permissions and Healthchecks are all set from. `tests/unit/test_workflows.py` and `test_scripts.py` hold the workflow, the scripts and that table to each other. |
| Still empty | `api`, `delivery` (M3 parts 3 to 5), `brain`, `mcp_server` (M4), `evaluation` (M6). |

### Database (Supabase Postgres; every table has RLS on with no policies)
- **0001:** `users`, `leagues`, `platform_credentials` (encrypted), `runs` (run log).
- **0002:**
  - `players` and `player_external_ids` (the crosswalk; `match_method` is primary | name_team | name_unique | override | unmatched)
  - `nba_games` (ESPN game ID, scoring period, tip UTC, standard team codes)
  - `league_settings_versions` (new row only when the rules' hash changes)
  - `teams`, and `roster_entries` (one snapshot per league per local date)
  - `matchups` (detail JSONB: daily points for ESPN, per-player weekly points for Sleeper)
  - `transactions` (items keep platform IDs; resolve through the crosswalk when reading)
  - `projection_snapshots` (unique `raw_key`) and `projection_rows` (per-game stats JSONB, `games`, `game_date` for per-game rows)
  - `injury_observations` (changes only)
- **0003:** `player_game_stats` (box scores, one typed `REAL` column per stat, ADR 0022), `raw_loads`, typed projection columns, read-path indexes.
- **0004:** `recommendations` (append-only; a trigger refuses UPDATE and DELETE).
- **0005:** `fact_sheets` (append-only) and `league_player_values` (replaced each run), the read models (ADR 0020).
- **0006:** `reports` (one row per league, kind and date; replaced on a rerun with its first id kept; deleted with its league or user; ADR 0026).
- **Planned:** 0007 in M3 part 3 (`users.auth_user_id`, `push_subscriptions`), 0008 in part 5 (`reports.notified_at`). Dev and prod are both at 0006.

### Raw store layout
`<source>/<dataset>/<YYYY-MM-DD>/<HHMMSSffffff>Z.json.gz` plus a `.meta.json` sidecar, under `data/raw/` on the laptop and at the top of the R2 bucket `fantasy-helper-raw` (the same keys and bytes in both, ADR 0023). League data lives under the dataset `leagues/<league_id>/<name>`. A morning set that saved and passed in full also writes a small marker under `fh/snapshot_complete/` (ADR 0028). Never overwrite.

## 5. Data source facts (learned the hard way, M1 spike)

Full write-up: `docs/spikes/m1-data-sources.md`.

**ESPN** (unofficial; base `https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{2027}`):
- **Public, no login:**
  - `…/segments/0/leaguedefaults/3?view=kona_player_info` with an `x-fantasy-filter` header: players, ownership, injury status, and **season** projections (`statSourceId 1`, split 0: `stats` totals with stat 42 = GP, `averageStats` per game). Projects only about 350 of 1,100 players. The filter `filterStatsForTopScoringPeriodIds {value:N, additionalValue:["002027","102027"]}` trims payloads; per-game actuals are `statSourceId 0, statSplitTypeId 5`.
  - `?view=proTeamSchedules_wl`: the schedule. Tips are UTC; **scoring period 1 = opening night, 2026-10-20**.
- **No per-day projections anywhere**, in league context or with projection filters. Per-game projections come from Sleeper.
- **League (cookies `espn_s2` and `SWID`):** views `mSettings+mStatus`, `mTeam+mRoster`, `mMatchupScore+mScoreboard`. Transactions: `mTransactions2` **one scoring period at a time with a type filter**, since the unfiltered view returns only recent lineup moves.
- **Accepted trades have no items**: they're on the proposal (`relatedTransactionId`), and **ESPN shows proposals only to the teams involved**. 6 of 10 trades resolve (all involving team 13). For every league trade, use the **activity feed**: `…/communication/?view=kona_league_communication`, topic `ACTIVITY_TRANSACTIONS`, message type 244 (ADR 0019, built in P9).
- **Code tables** (from `espn-api`, ADR 0016):
  - stat IDs: 0 PTS, 1 BLK, 2 STL, 3 AST, 6 REB, 11 TO, 13 FGM, 14 FGA, 15 FTM, 16 FTA, 17 3PM, 18 3PA, 19 FG%, 20 FT%, 42 GP
  - slots: 0–4 positions, 5 G, 6 F, 11 UTIL, 12 BENCH, 13 IR
  - team IDs 1–30 → standard codes; the schedule's abbreviations differ (GS, NO, NY, SA, UTAH, WSH)
- Transaction IDs are UUIDs (the redactor pseudonymizes them consistently). Add limits are a per-day rate when `matchupLimitPerScoringPeriod` is set.

**Sleeper** (official `/v1` plus undocumented `api.sleeper.com/projections`):
- `/v1/state/nba` (season "2026", week). `/v1/players/nba`: **at most once a day** (Sleeper's request). **`espn_id`/`yahoo_id` are empty for every NBA player**, but `birth_date` is filled for 1,841 of 1,852 active players.
- **ESPN birth dates** come from ESPN's public sports API, not Fantasy: `https://sports.core.api.espn.com/v3/sports/basketball/nba/athletes?limit=20000&active=true` (757 athletes, 749 with `dateOfBirth`; same IDs as ESPN Fantasy; it covers every projected and every rostered ESPN player). It's the `espn/athletes` morning snapshot.
- Projections are **RotoWire's**:
  - `projections/nba/2026` holds season **per-game averages** (`gp: 1`)
  - `projections/nba/2026/{week}` holds **one row per game** with a date; an **empty `stats` row means not expected to play** (kept as 0 games)
- League: `/league/{id}`, `/users` (display and team names: redact), `/rosters` (`starters` align with non-BN `roster_positions`; `reserve` = IR; `taxi` excluded), `/matchups/{week}` (only weekly points per player; **no lock-in data**, so M5 needs the "I locked / I passed" button), `/transactions/{week}`.

## 6. Engineering practices and gotchas

**Commands:** `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format .`, `uv run mypy` (strict), `uv run lint-imports`, `uv run alembic upgrade head`, `uv run fh doctor`. There are 434 tests on `main` at `f7544b2` (404 run without a database; the other 30 need `FH_TEST_DATABASE_URL` and are skipped without it): unit, golden (`tests/golden/`, the hand-checked scores), contract (`tests/contract/`) and integration (DB). In a fresh worktree, `uv` and `gcloud` may not be on the shell's PATH: they live in `~/.local/bin` and `~/google-cloud-sdk/bin`.

**This Mac and Homebrew (2026-11-14).** Adam's laptop is a 2018 Intel MacBook Pro on macOS 14. Homebrew moved Intel Macs to its lowest support tier in 2026 (no new prebuilt packages; support ends 2027-09-01), so **any `brew install` or `brew upgrade` that touches an updated package compiles from source**: a `brew upgrade` spent hours on LLVM and Rust just to update uv. So:
- **Never suggest a plain `brew upgrade`.** Run `brew outdated` and upgrade one package by name, only if it's needed.
- **Big tools come from their own installers:** uv from Astral's installer in `~/.local/bin` (update with `uv self update`), and `gcloud` from Google's installer in `~/google-cloud-sdk` (update with `gcloud components update`). Homebrew keeps only small things (`gh`, `postgresql@17`, `python@3.14`).
- **The laptop's snapshot schedule is gone (2026-11-16).** If it's ever reinstalled (`scripts/snapshot-schedule.sh install`), the plists store uv's absolute path from that moment, so moving uv again means reinstalling the schedule (with the new uv first on PATH) before removing the old one, or the snapshots silently stop.
- The project's Python is the python.org 3.12 framework build, not Homebrew's.

**Database tests locally** need a throwaway Postgres. Homebrew's `postgresql@17` is installed but **isn't a service**:
```bash
PGBIN=/usr/local/opt/postgresql@17/bin; export LC_ALL=en_US.UTF-8; D=<scratch dir>/pg
"$PGBIN/initdb" -D "$D/data" -U fh --auth=trust -E UTF8
"$PGBIN/pg_ctl" -D "$D/data" -o "-p 54329 -c unix_socket_directories='' -c listen_addresses=localhost" -l "$D/server.log" -w start
"$PGBIN/createdb" -h localhost -p 54329 -U fh fh_test
FH_TEST_DATABASE_URL=postgresql://fh@localhost:54329/fh_test uv run pytest
"$PGBIN/pg_ctl" -D "$D/data" -m fast stop
```
TCP only, because long scratch paths exceed the 103-byte Unix socket limit. `LC_ALL` is required, or the postmaster refuses to start. DB tests refuse any database whose name lacks "test".

**Fixtures:** `uv run fh fixtures record` re-records 22 redacted fixtures to `tests/fixtures/recorded/`. It verifies its own output (no original name, ID, cookie or league ID may remain) and writes nothing otherwise. An independent check found 0 of 148 identifying values. **Contract tests pin exact counts from the 2026-11-06 recording** (155 rostered, 118 transactions, 90 matchups, …), so re-recording means updating those numbers in the same change. Fixture files must stay under 500 KB (pre-commit hook): the recorder empties player stat arrays in roster and matchup files.

**Migrations:** numbered by hand (`--rev-id 0003`), generated with autogenerate against a scratch DB at the previous head. Every new table needs `ALTER TABLE … ENABLE ROW LEVEL SECURITY` (a test enforces it). CI runs `alembic check`.

**Gotchas that already bit us (don't relearn them):**
1. **pydantic-settings with `populate_by_name=True` matches env vars by field name too.** The shell's `ANTHROPIC_API_KEY` filled `anthropic_api_key`. Settings match by **alias only**, plus `env_ignore_empty=True` (`KEY=` means unset). Tests cover both.
2. **httpx drops a URL's own query string whenever `params=` is passed, even `{}`.** `get_raw` passes `params or None`; repeated keys go as lists (`{"view": ["mSettings", "mTeam"]}`).
3. **SQLAlchemy doesn't order inserts by foreign key without `relationship()`.** The crosswalk uses bulk Core inserts: players, then links.
4. **Typer's pretty tracebacks show local variables**, which can hold secrets, so they're disabled.
5. **Redaction false positives:** a 4-letter team abbreviation inside the ESPN field `isAutoReactivate`, and leaguemates' first names matching NBA players. Values under 8 characters only match as whole words; members are tracked by full name and display name.
6. **gitleaks `generic-api-key`** treats an empty `KEY=` followed by a long-token line as a secret. In `.env.example`, empty secret keys sit at the end of their block, followed by a blank line or comment.
7. **zsh doesn't word-split `$VAR`.** Inline `$(git rev-list --all)` instead.
8. **The Supabase direct connection is IPv6-only** and unreachable here and from GitHub Actions. Use the **session pooler** URL. psycopg never prepares statements (`prepare_threshold=None`), so the transaction pooler would work too.
9. **langchain-anthropic defaults `with_structured_output` to forced tool calls**, which current Sonnet/Opus reject. Always pass `method="json_schema"` (ADR 0015).
10. **Ruff S105** flags enum values containing "pass" (lock-or-pass); the code uses targeted `# noqa: S105` with a reason. Curly apostrophes in strings trip RUF001, so write `’`.
11. Scripts that read downloaded or raw data run with `uv run python -I` from a scratch folder, and print structure only.
12. **Supabase Free, checked against its docs on 2026-11-06:**
    - 500 MB database size, a billing quota with Fair Use restrictions when exceeded
    - 2 GB fixed disk, read-only at 95%
    - 500 MB RAM; two active projects
    - pauses after a week idle
    - **no automatic backups**

    The dev project is in `us-east-1`.
13. **Measured latency and size (2026-11-06; ADRs 0020, 0021):**
    - **From the Mac:** about 34 ms per query round trip and about 600 ms per new connection. Hot queries take 0.3–2.7 ms on the server.
    - **One synthetic season:** 720,000 projection rows. At JSONB's real size (about 380 bytes a row) that's about 275 MB; typed columns cut it to 173 bytes a row.
    - **Five seasons:** 2 GB, so past seasons go to Parquet in R2 (about 13 MB a season of projections).
    - **Bloat:** a bulk insert that fails and rolls back leaves dead rows that inflate table size until `VACUUM FULL`. Measure sizes on a clean load.

## 7. Exactly what's next

**The plan is [m3-plan.md](m3-plan.md).** It has six phases in order, each with what to build, what Adam does, how to check it, and the traps. Start at its status table. The rest of this section is the record of what was found along the way.

### Found on 2026-11-16
- **The overlap ended (Adam, 2026-11-16).** He removed the laptop's launchd schedule and the other schedules, and PR #19 (merged, deployed) dropped GitHub's schedule, `pipeline/cron.py` and the `--cron` option; Cloud Run is the only timer. The count had reached 3 of 4 on-time runs; he didn't wait for the fourth. What's left for him is on the laptop (see the snapshot bullet in §3).
- **The chain ran on its own at 06:17 Eastern and all three jobs succeeded** (snapshot 22 seconds, `daily` 1 min 40, `weekly` 1 min 14; no league errors). That proves the hand-over and the permissions from the scheduler. Overlap count: 3 of 4 on-time runs; the fourth is 17:17 on 2026-11-16. Healthchecks showed all four checks with recent, on-time pings.
- **The run summaries' numbers.** `daily` loaded 192 box scores and 4,482 projection rows, which is everything new since the last load (Sunday's games and three snapshot sets, two of them GitHub's late copies). `weekly`, two minutes later, loaded 0 and 0. Both are right; the runbook's "Day to day" says so.
- **Database size, measured 2026-11-15 after the history load:** 45 MB of 500 MB. Two tables held 32 MB: `projection_rows` 18 MB (92,988 rows) and `player_game_stats` 14 MB (11,019 rows), about 12 MB of that bloat, because every ingest re-saved the whole season's box scores even when nothing had changed. **PR #17** (`3e9eff9`, merged 2026-11-15) writes only rows that changed. `VACUUM FULL player_game_stats` reclaims the space (Adam's command, away from 06:17 to 07:00; not confirmed run). About 9 MB is probably Supabase's own baseline. The estimate for a full season is about 180 MB (36%). The overlap doubles projection snapshots (104 loaded against about 50 expected), and that ends with Phase 1. Levers if needed: ADR 0021.
- **Open: one day's startable count in the weekly report was one lower than the league's real lineup** (Adam read the report on 2026-11-16; the cause isn't found). From reading the code, a player on the roster can lose a day's game three ways: he has no projection at all (`_ProjectionBook.for_game` returns `None`, so the game is dropped), he has no profile row (`facts/weekly.py` drops him from the roster), or the schedule is missing his team's game. A player flagged out is **not** the cause: he becomes a game with zero play probability, which still takes a slot and counts as startable, so `startable` overcounts when someone is out (a separate, smaller defect). To find the cause, load the week's sheet (`facts.weekly.load_weekly`) against a copy of prod's data and list each active player's games for that day. That needs prod data, so it's Adam's command or his say-so (§3). It is also item 1 of the roadmap's planner direction (show who was counted and why).
- **The weekly plan's limits are a direction, not a bug list:** it counts only players not on injured reserve, treats a current "out" as out for the week where the provider has no per-game row, scores every swap as if made at the start of the week, and spends the add limit all at once. Adam wants day-by-day planning and a buddy that knows how leagues work: roadmap, "Direction: a smarter weekly planner, and a buddy that knows how leagues work" (placement is m3-plan.md decision 7). Keep specific players and leagues out of the docs; record the general idea.

### Found on 2026-11-15 (after PR #16 merged)
- **The deploy answered both open questions.** The deployer's job-level role can run `migrate` and wait for it (prod went from empty to 0006 inside the deploy), and all five jobs took their accounts, secrets and chain settings. The fallbacks in ADR 0027 and the runbook weren't needed.
- **UPDATE, evening of 2026-11-15: the chain has run.** Prod has Adam's leagues and all history, and a hand-started morning snapshot handed over to `daily` and then `weekly`; all succeeded. (Original note:) **The chain was wired but hadn't handed over yet.** The policies are right (`fh-jobs` → `daily`, `fh-reports` → `weekly`). The first real hand-over is the first morning snapshot that completes on the new image.
- **That happens on its own at 06:17 Eastern on 2026-11-16.** If prod has no user by then, `daily` exits 1 ("no user yet") and Healthchecks alerts; nothing is written. So the rollout's steps 4 to 6 and 9 belong on 2026-11-15. **Update 2026-11-16: they were done, and the chain started on its own at 06:17 and succeeded.**
- **A trap in the rollout, now fixed in the runbook:** on rollout day the 06:17 snapshot ran the old image and wrote no completion marker, so starting `daily` by hand would wait and fail. Start `snapshot-morning` by hand instead: it writes the marker and runs the whole chain.
- **`fh ingest` is one transaction.** A history load that hits the job's time limit saves nothing and starts over; the runbook used to say it picks up where it stopped.
- **Google Cloud's project ID and number are in the public deploy logs.** GitHub prints an environment's variables in a run's log, and the repo's Actions logs are public. The repo itself doesn't hold them. They identify the project and grant nothing (deploys are tied to this repo, `main` and `deploy.yml`), so this is a choice for Adam, not a leak (m3-plan.md, decision 6).
- **Cloudflare now says to start new projects on Workers, not Pages.** ADR 0009 names Pages; part 4 proposes Workers static assets as an Update to it (m3-plan.md, Phase 3).
- **Supabase is retiring the old `anon` and `service_role` keys at the end of 2026.** The phone app uses the publishable key. If what's in Bitwarden from 2026-11-07 is the old `anon` key, copy the publishable one before part 4.
- **The schedule slipped about ten days.** Roadmap v0.6 proposes new dates; the hard stop (2026-12-15) holds if M4's brain work runs beside the phone app.

### Found on 2026-11-14
- **Every account M3 needs exists** (setup sections 1–7, checked 2026-11-14): Google Cloud project `fantasy-helper` in `us-east4` with billing, a $5 budget and the four services; Healthchecks with its ping key in `prod`; the iPhone is on iOS 16.4+; the prod ntfy subscription works.
- **GitHub's scheduler is too late to use for timed jobs.** From 2026-11-07 to 11-14 every cloud snapshot saved and none was dropped, but every one started hours late: mornings at 11:40–16:30 instead of 06:17, evenings at 20:00–23:10 instead of 17:17, often after tip-off. The laptop's launchd copies were on time and are in R2. `daily.yml` and `weekly.yml` on the same scheduler would deliver the morning report in the afternoon, so **the trigger has to change before part 2's scheduled runs are useful**. **Decided (ADR 0025, Adam approved the plan 2026-11-14):** timed jobs run on **Cloud Run Jobs started by Cloud Scheduler**, with Healthchecks pings in `fh snapshot`, keyless deploys from `main` (`.github/workflows/deploy.yml`), secrets in Secret Manager, and the setup in `scripts/gcp-setup.sh` / `scripts/gcp-secrets.sh` ([runbooks/scheduled-jobs.md](runbooks/scheduled-jobs.md)). **Live since 2026-11-14** (PRs #12 and #14): both jobs deployed, scheduled at 06:17 and 17:17 Eastern, tested end to end (evening 4/4, morning 9/9 into R2), and the Healthchecks schedules set. The overlap count starts with the first scheduled run, 2026-11-15 at 06:17. After four on-time runs in a row: cutover step 5 (remove launchd, the laptop's own R2 key, drop the GitHub schedule). One manual start ran twice (Cloud Scheduler is at-least-once): harmless, watch for it on scheduled runs. Part 2's daily and weekly jobs use the same mechanism.
- **This Mac and Homebrew:** see §6. uv moved to Astral's installer and `gcloud` came from Google's, both because Homebrew compiles from source on this Intel Mac.

### Found on 2026-11-07
- **The laptop ran M1 code. Fixed:** its main checkout is at `02c3f0f` (M3 part 1) since 2026-11-07. With launchd gone (2026-11-16) a stale checkout no longer affects the snapshots, but still `git pull` before running `fh` commands there.
- **Sleeper re-issued its season projections** (06:30 run, 2026-11-07): 1,334 rows that held only ADP fields went empty, `gp`, `dd`, `td` and `dreb` were dropped, minutes became realistic, and 564 players have a points projection (524 the day before). Still per-game averages. The old check (half of all rows must have stats) flagged it; it now requires 300+ players with a points projection, like the ESPN check. None of Adam's leagues score the dropped stats.

### Left from M2 (none blocks M3)
- **ESPN matchup periods:** map `scheduleSettings.matchupPeriods` to scoring days **before the All-Star break** (February). Until then a period that started before this calendar week is flagged (`period_mismatch`), with no banked points or win probability.
- **Mid-week win probability for lock-in leagues:** needs lock data. Lock detection is parked (Adam, 2026-11-06); the plan is the one-tap "I locked / I passed" button in the PWA (lock-in v2, M5).
- **Categories-league valuation:** P9. The 9-cat league gets games and roster facts only.
- **Snapshot days are permanent:** Cloud Run is the only timer since 2026-11-16, so a run that never starts is caught only by Healthchecks (the watchdog's pings have been live since 2026-11-14 and its schedules are set by the script). The manual fallback is **Actions → Snapshot → Run workflow** (a set is safe to save twice; rule 8).

### M3: Phone app and delivery (planned Nov 7–12; now expected to end about Nov 23)
Approved by Adam on 2026-11-06, in five parts, each a checkpoint he approves and, since part 1, its own branch and PR. **Parts 3 to 5 are laid out step by step in [m3-plan.md](m3-plan.md)** (Phases 2 to 5), which also moves two things from what this table first said: "Enable notifications" is built with the push keys in part 5, because the button can't work before they exist, and the Healthchecks pings and size alert already shipped with parts 1 and 2.

| Part | Builds | Needs from Adam | Status |
|---|---|---|---|
| **1. Snapshots to the cloud** | `R2RawStore` (`storage/r2_store.py`), `fh raw upload`, `fh doctor --only raw`, `.github/workflows/snapshot.yml` (now a manual fallback), once-a-day guard for Sleeper's player list, crash alerts. ADR 0023 | Setup sections 1, 3 and 4 (**done**); then [runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md) | **Merged** (PR #4, 2026-11-07), **live**, on Cloud Run since 2026-11-14 (ADR 0025). Cutover steps 1–3 done; the overlap **ended 2026-11-16** (the Mac's schedule and GitHub's are removed; Adam's laptop steps remain) (plan, Phase 1) |
| **2. Prod database and scheduled runs** | Migration 0006 (`reports`; `push_subscriptions` moved to part 3), the `migrate`, `daily` and `weekly` Cloud Run jobs, **chained after the morning snapshot** (ADR 0028: complete snapshot marker, each job starts only the next, Healthchecks checks from `scripts/jobs.conf`), a run lock, `fh migrate`, `fh report open`, `scripts/fh-prod.sh` (prod secrets never on disk), `scripts/healthchecks-setup.sh`. ADRs 0026, 0027, 0028; [runbooks/prod-database.md](runbooks/prod-database.md). Dependabot PRs that change the deploy path wait for a person (ADR 0024, Update). The snapshot job doesn't log runs to the database (Healthchecks and Cloud Logging already record them) | Two secret values and the ESPN cookies typed once, then the Healthchecks API key (the runbook's steps 2, 5, 9) | **Merged** (PR #16, 2026-11-15) and deployed; prod is at 0006. **Rollout: done 2026-11-16** (all 11 steps; the chain ran on its own at 06:17; plan, Phase 0) |
| **3. API on Cloud Run** | FastAPI (reports, push subscriptions), Supabase JWT checked against its public keys (JWKS), a sign-in tied to our user by id (migration 0007), every query scoped to the user, the same Docker image started with `fh serve`, its own account with no encryption key, max 2 instances | The Supabase project URL, the email template, his own sign-in, one secret value | Code written on branch `m3-part3-api` (bf2b544, no PR); cloud setup and Adam's inputs still to do (plan, Phase 2) |
| **4. The phone app** | `web/` PWA: sign-in by code, Today and This Week tabs, Home Screen install, types from OpenAPI, hosted on Cloudflare (Workers static assets proposed, instead of Pages) | Two decisions (the host, a domain), connect Cloudflare to GitHub, the iPhone | Planned (plan, Phase 3) |
| **5. Push, ops, exit test** | VAPID keys straight into secrets, Enable notifications, "report ready" push sent by the report jobs after commit, nightly backup to its own locked bucket and a restore drill, bucket lock on the public raw prefixes, broken-run test, laptop-closed test | A backups bucket and token, the lock rules, two decisions | Planned (plan, Phases 4 and 5) |

**Decided with Adam (2026-11-06):**
- **Sign-in by 6-digit email code, not a magic link.** A Home Screen app on iOS doesn't share Safari's session, so a tapped link would sign in Safari, not the app. Same Supabase Auth; the email template shows `{{ .Token }}`.
- **The prod registry starts empty at go-live.** Dev's calls since 2026-11-06 stay in dev as test data. All snapshot history loads into prod from R2.

**How M3 merges:** the deploy runs only from `main`, and `prod` is limited to `main`, so each part merges as its own PR, rather than one PR at the end. A merge that touches `src/`, the `Dockerfile` or `deploy.yml` deploys within minutes and runs any new migration against prod before anyone looks, so migrations are additive (ADR 0027). A docs-only merge deploys nothing.

**Done when:** a scheduled run, with the laptop closed, pushes the daily report (lock-or-pass cards) and the weekly overview to his phone, and they open after sign-in; a deliberately broken run produces an ops alert; a backup restores into a scratch database.

### Dependency updates (ADR 0024)
Dependabot opens grouped PRs (security fixes as they land; version updates Mondays 07:00 Eastern, 7-day cooldown, no Python majors), and `dependabot-auto-merge.yml` merges each one once CI passes. It refuses unless `main`'s ruleset requires both CI jobs ([runbooks/dependency-updates.md](runbooks/dependency-updates.md)); the ruleset `main` was created on 2026-11-07, with an admin bypass. **First run, 2026-11-07 (checked):** within five minutes of the merge, Dependabot opened PRs #6 (setup-uv 7.6.0 → 10.3.3), #7 (six Python updates) and #8 (the `uv_build` range), and each merged itself only after both CI jobs passed. GitHub doesn't rerun CI on `main` after these merges (ADR 0024), so the combined `main` was checked by hand: lockfile consistent, all checks and 310 tests pass. **Still to see:** the first security PR, whose job log should show the major-version `ignore` doesn't apply to security updates.

### Tabled from the PR #4 review (2026-11-07)
- **4, shared crash guard: done in part 2** (`cli/_common.py`: `watched`, `crash_guard`, `REPORTED_EXIT`; `cli/_runs.py`).
- **8 and 9, a reused once-a-day result** hides a flagged payload and drops freshness. Revisit in **part 5**, with the stale-source check.
- **6, R2's `BadDigest` is treated as permanent.** Revisit if one shows up in the run log, or with other R2 hardening in part 5.
- **7, a 409 `ConditionalRequestConflict` reads as "already exists".** Each key has one writer; revisit if two writers can ever target the same key.
- **10 and 12, `fh raw upload` efficiency** (re-HEADs listed objects; orphans checked against a list). Fold into the next change to `raw_upload.py`.

### Open items
- **Weekly report:** a day's startable count was one lower than the league's real lineup on 2026-11-16, and `startable` counts a player who is out as a started game. Find and fix both (above, "Found on 2026-11-16").
- **Security, R2:** each machine gets its own R2 key at the end of the cutover (step 5; the laptop's needs write, because ingest saves league responses). Part 5 adds a bucket lock on the **public** dataset prefixes only: league data must stay deletable for a beta friend who leaves (ADR 0023, Update 2026-11-07).
- **Not verified by Claude:** prod Supabase's region (the doctor shows it, rollout step 4, which is Adam's to run: see §3), its plan, and the sign-ups-off setting (Adam did section 2). Part 3's smoke script proves sign-ups are off: a code for an unknown email must be refused.
- **Before M4's first model call:** the cloud jobs have neither the Claude key nor the LangSmith key (rule 3). Both go into Secret Manager for `fh-reports`, not into GitHub's `prod` as [runbooks/m3-account-setup.md](runbooks/m3-account-setup.md) section 7 first said.
- **Q6:** how fast projections react to injuries. Analyze `projection_rows` against `injury_observations` once a week or so of snapshots exists.
- Beta friends and leagues (needed by M7). The RatBall note goes out only if RatBall data is used.
- The 821 unmatched Sleeper players are all non-rostered and **aren't in ESPN Fantasy's player pool**, so there's nothing to link them to. Overrides stay empty until ingest warns about a rostered one. Ingest reports linked pairs with conflicting birth dates (2 known data errors: Gortman, Edey); that's informational.
- ESPN activity-feed trades: P9. Categories-league logic: P9.
- **Storage levers (ADR 0021),** if the 60% size alert fires: store only changed projection rows, and store only roster changes. Season rollover to the R2 archive: P11.
- **Before M7:** friend invites need a custom email sender for Supabase Auth (the built-in one only reaches organization members, 2 emails an hour), which needs a domain Adam owns.

## 8. Session history (for orientation)

- **2026-10-16:** read the 25-page spec; architecture, repo structure and roadmap drafted.
- **2026-10-19:** Adam's review. User and league data go in the database behind auth (not `.env`); timeline graphs for takes, calls and trades added; M3 split; Supabase from day one; decisions D1–D10.
- **2026-10-25 to 11-06:** M0 built. Supabase dev, LangSmith and ntfy set up together (pooler URL, iPhone notification fix); PR #1 audited for leaks and merged.
- **2026-11-06:** M1. Snapshots started and scheduled; `fh league add` for three leagues; ESPN and Sleeper explored; the redacting recorder; mappers; identity; tables; `fh ingest` run live twice; ADRs 0016–0019; roadmap v0.4; this handoff doc. PR #2 merged.
- **2026-11-06 (pre-M2 review):** Adam asked for low latency for agents and a future chat, and for room to keep history for future years. Latency was measured against Supabase dev, and one and five synthetic seasons were benchmarked locally. Result: ADR 0020 (read models, few batched tools, in-process MCP, one region) and ADR 0021 (typed projection columns, a size alert, the season archive to R2, nightly backups). Sleeper lock-in moved to M2, as cards in the daily report. The M3 account-setup runbook was written for Adam.
- **2026-11-06 (M2):** one box score for every league (ADR 0022) and scoring verified against both platforms; run events and retries; lock-in v1 in an engine-only daily report (lock detection parked after Adam's real locks fit no rule); the weekly overview with FactSheet v1 and read models; the daily reconciliation; a mid-week win probability for daily-lineup leagues (exact against ESPN's daily totals). PR #3 opened with Auto-fix on; a `/code-review` posted 15 findings as inline comments, all fixed in one commit, each comment answered and resolved.
- **2026-11-07 (M3 part 1):** cloud snapshots built (ADR 0023) and verified live against R2 (conditional writes enforced; the secret key had been mistyped and was re-derived). Sleeper re-issued its projections, and the check was fixed. PR #4 opened with Auto-fix; a `/code-review` posted 14 findings (7 fixed, 1 partly with the rest moved to part 2, 6 tabled), every thread answered and resolved; merged. Adam set up the `prod` environment, R2, the prod encryption key and ntfy topic, Supabase prod, and Bitwarden; the repo stays public (roadmap). Dependabot with guarded auto-merge (ADR 0024, PR #5) and the `main` ruleset; the first three Dependabot PRs merged themselves. The overlap was revised: the laptop stays on its own disk until the watchdog. The accounts-and-secrets inventory was started.
- **2026-11-14 to 11-15 (M3 part 1 finished, part 2 built):** every M3 account made (Google Cloud, Healthchecks, iPhone). GitHub's scheduler ran every snapshot hours late for a week, so timed jobs moved to Cloud Run Jobs + Cloud Scheduler (ADR 0025, PRs #12, #14, #15), reviewed (14 findings fixed) and rolled out live. The same night a false Healthchecks alert came from schedules typed into the website in UTC. Part 2 (ADRs 0026, 0027) saves reports in the database, builds them in the cloud every morning behind a freshness gate and a run lock, migrates prod from the deploy, and sets Healthchecks schedules by script. Adam's laptop is Intel, where Homebrew compiles from source: uv and gcloud come from their own installers (§6).
- **2026-11-15 (after PR #16; a planning session on Claude Fable 5.2):** PR #16 merged and its deploy watched: green, prod migrated to 0006 by the deploy, the chain wired. The cloud's state was read back with `gcloud` (jobs, policies, schedules, run history, logs), which turned up the rollout-day marker trap and the 2026-11-16 06:17 deadline. Parts 3 to 5 were designed and written down as [m3-plan.md](m3-plan.md), with the outside facts they rest on checked against the vendors' docs that day; the roadmap was re-baselined as v0.6 (proposed); this handoff, the rollout runbook and the stale spots in the other docs were brought up to date. No code changed.
- **2026-11-15 (evening) to 2026-11-16 (rollout and follow-ups):** Adam added the three leagues and the history loaded into prod (8.5 minutes, 101 projection snapshots, 90,508 rows). The chain ran by hand that evening and then on its own at 06:17 on 2026-11-16. A size check found box-score bloat, fixed and merged as PR #17. The chat buddy was recorded as a core goal (M4b) and per-user reports as a direction, a plain-English guide was written for Adam, and the API's code was written on `m3-part3-api`. On 2026-11-16 Adam finished the rollout's steps 9 to 11 and read the weekly report, which produced the smarter-planner direction and the unexplained one-slot count difference.
