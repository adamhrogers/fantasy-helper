# Session handoff: everything a new session needs

**Last updated:** 2026-11-06, after the pre-M2 review of latency and storage (ADRs 0020, 0021; roadmap v0.5). M1 was merged as PR #2.
**Read this first**, then `CLAUDE.md` (rules), `docs/roadmap.md` (milestones), `docs/architecture.md` (design) and `docs/decisions/` (ADRs). This file is the narrative glue: what exists, how we work, what we learned the hard way, and exactly what comes next.

The repo is **public**, so this file holds no secrets or personal data. League names and IDs, the owner's email, the Supabase project ID and the ntfy topic live in `.env` and the database. `uv run fh league list` shows the leagues.

---

## 1. The project in one paragraph

A fantasy basketball assistant for Adam (customer one), then a friends beta, then a paid launch for 2027-28. Every morning it sends a short briefing to his phone, with specific moves tuned to each league's own scoring, built on his own takes (a watchlist of beliefs), and it **grades every call it makes** against a baseline, all season. It doubles as a portfolio case study (deployment strategist; "the Blitzy pattern scaled down": an orchestrator with specialized agents and validation). The source spec is *Fantasy Basketball Helper: Product Spec* (2026-10-15, 25 pages; it lives outside the repo).

**Core principles** (never broken; details in CLAUDE.md):
- **Code computes every number; models only argue.** The engine makes candidate moves with expected values; LLMs read a FactSheet, select, explain and challenge; guardrails reject ungrounded numbers.
- **Evidence over opinion.** Every call is logged before tip-off and never edited; inputs are versioned snapshots; grades come later, against a baseline.
- **Adapters isolate platforms.** ESPN's API is unofficial; when it changes, only `adapters/espn/` changes.

## 2. Where things stand (2026-11-06)

| Milestone | Status |
|---|---|
| M0 Foundation | **Done, merged** (PR #1) |
| M1 Data spike | **Done, merged** (PR #2) |
| Pre-M2 review | **Done**: latency (ADR 0020), storage budget and season archive (ADR 0021), lock-in moved to M2 |
| M2 Scoring, weekly overview, **Sleeper lock-in** | **In progress** on branch `m2-scoring`. Done:
- box scores: `espn.game_logs`, migration 0003, backfilled from opening night (6,935 rows)
- typed projection columns and read-path indexes (dev database at 0003)
- **one box score for every league** (ADR 0022, CLAUDE.md rule 9): complete for every game played, internally consistent, and every league's settings mapped onto it, missed shots and fouls included
- `engine/scoring.py` with golden and property tests, verified against ESPN (155/155) and Sleeper (417/417 on Sleeper's own stats; 405/417 on ESPN's, the 12 from one-stat feed differences): [spikes/m2-scoring-check.md](spikes/m2-scoring-check.md)

- **lock-in v1 built (uncommitted at first):** `engine/distributions.py`, `engine/lock_in.py`, `facts/lock_in.py`, the append-only `recommendations` table (migration 0004, a trigger refuses UPDATE and DELETE; dev is at 0004), `fh run daily [--dry-run] [--open]`, and the engine-only daily report as a local page. The first 8 calls were logged in dev on 2026-11-06 at 7:54 pm ET.
- **Locks:** Adam gave his real week-3 locks. Sleeper's weekly numbers fit no rule against them (they flip between pulls minutes apart), so cards show every open decision and the next lock-in step is a **user-set lock flag on the roster** (one tap in the PWA, or a CLI command until then).

Next: the reconciliation check, then the rest of the engine (schedule, valuation, streaming, win probability), FactSheet v1 and the weekly report. |
| M3 Phone app + delivery | **Next, in parallel with M2** (Nov 7–12). Adam is creating the accounts ([runbooks/m3-account-setup.md](runbooks/m3-account-setup.md)). |
| M4–M8, P9–P12 | See `docs/roadmap.md` v0.5. Friends beta **Nov 29**, hard stop **Dec 15**. |

### What runs today
- **`fh snapshot`** runs on Adam's Mac through launchd: the morning set at **06:30** and the pre-tip evening set at **17:30** local time (America/New_York). It saves raw ESPN and Sleeper public data to `data/raw/` (git-ignored). History has accumulated since **2026-11-06**. Check it with `scripts/snapshot-schedule.sh status`; logs are in `~/Library/Logs/fantasy-helper/` (JSON lines). A sleeping Mac runs missed jobs on wake; a Mac that's shut down skips them, until M3 moves this to the cloud.
- **`fh ingest`** is run by hand for now. It loads every not-yet-loaded snapshot plus fresh league data into Supabase, takes about 30 seconds, and is idempotent. First live run: 1,100 ESPN players; **all 1,852 active Sleeper players** (the Sleeper league has 142 rostered, all linked to ESPN, and **1,710 free agents**, 889 linked to ESPN and 410 with Sleeper projections); 1,200 games; 9 projection snapshots (8,490 rows); 226 injury changes; full rosters, matchups and transactions for all three leagues.
- **Supabase dev project**, migrated to **0002**. There's no prod project yet (M3).
- **LangSmith tracing** works (`fh doctor` sends a smoke trace). **ntfy run events** reach Adam's phone: every run sends started, steps and success quietly and failures loudly, and it's ops only; product notifications come from the PWA ([runbooks/run-events.md](runbooks/run-events.md), added 2026-11-06). iPhone notifications needed a settings fix, which is done.
- **GitHub:** `adamhrogers/fantasy-helper`, public. CI runs lint, mypy, contracts, tests on Postgres 17, `alembic check`, and a gitleaks full-history scan. Adam was asked to run `gh secret set LANGSMITH_API_KEY` so CI sends a smoke trace; **unconfirmed**, and the step is skipped (not failed) without it.

### Adam's three leagues (all 10 teams, season 2026-27 = `season 2027`)
| Fixture alias | Platform / format | Adam's team | Key rules |
|---|---|---|---|
| `espn_points` | ESPN, H2H points | team **13** | PTS +1, AST +2, REB +1, STL +4, BLK +4, TO −2, FGM +2, FGA −1, FTM +1, FTA −1, 3PM +1. Daily lineups (`INDIVIDUAL_GAME`). Adds: **0.571 a day** (4 per 7-day matchup). Slots: PG, SG, SF, PF, C, G, F, 3 UTIL, 4 BENCH, 2 IR. |
| `espn_categories` | ESPN, H2H most categories (9-cat) | team **12** | PTS, BLK, STL, AST, REB, TO (reversed), 3PM, FG%, FT%. Adds 1.0 a day. 3 BENCH, 3 IR. |
| `sleeper_points` | Sleeper, points, **lock-in** (`game_mode 1`, confirmed by Adam) | roster **1** | PTS +1, REB +1, AST **+2** (the spec said 4; the settings win), STL +3, BLK +3, TO −2, FGM +1, FGA −0.5, FTM +1, FTA −1, 3PM +0.5; **bonuses +2 at 40 points and +2 more at 50**. Slots: PG, SG, G, SF, PF, F, C, 3 UTIL, 3 BENCH, 2 IR. |

The spec said "friends beta not in Adam's competitive ESPN leagues; possibly the Sleeper league."

## 3. How we work with Adam

- **Plans first, explicit approval for anything big or outward-facing.** He reviews plans and says "go for it" before code. **Every commit, push, PR and installed background job was explicitly approved** ("Commit it", "Install it"). Ask with a short question and buttons; don't assume. Approval doesn't carry over to the next action.
- **One branch and one PR per milestone** (`m0-foundation`, `m1-data-spike`), merged into `main` **by Adam**. Commits within a milestone are made at checkpoints he approves ("M1 (part 1)", "part 2", …).
- **Commit footer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **PR body footer:** `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. After opening a PR, use the app's PR tools (get_status / bind) and offer Auto-fix; Adam turned it on for PR #1.
- **Secrets never pass through chat.**
  - Keys are generated straight into `.env` without being printed.
  - ESPN cookies are typed at `fh league add`'s hidden prompt.
  - GitHub secrets are set by Adam with `gh secret set`.
  - Scripts that touch real league data print **structure and counts only**, never member names.
- **Style he likes:** plain language, concise, step-by-step for anything he has to do by hand, with commands in their own fenced `bash` blocks. He asks "what is X?" questions; answer them directly. He isn't attached to specific tools and accepts reasoned recommendations.
- **He wants the reasoning and the honest state:** what was verified, what wasn't, and false alarms called false alarms. He specifically asked for a leak audit before merging M0. It was clean: `.env` never committed, none of his secret values in history or the PR, gitleaks and GitHub secret scanning clean.
- **Parallelism:** sub-agents and parallel sessions are fine once a plan is approved. M2 and M3 are the natural pair to split (they share no files).

## 4. Architecture as built (quick map)

Layers, enforced by import-linter in CI: `cli` → `api | mcp_server` → `pipeline` → `evaluation` → `brain | reports | delivery` → `facts | registry` → `engine | identity` → `storage | adapters` → `observability` → `config` → `domain`. The engine is pure (no storage, adapters or I/O); the brain sees only FactSheets and MCP tools. **A new top-level package must be added to the contracts in `pyproject.toml`.**

| Area | What exists (M0 + M1) |
|---|---|
| `domain/` | Frozen Pydantic models, UTC-only (`UtcDatetime`). `league.py`: `LeagueRules` (hashed), `ScoringRules` with `bonuses`, ratio `CategoryRule`s (FG%), named `RosterSlot`s (`SLOT_POSITIONS`), `AcquisitionLimits.per_matchup_per_day`. `records.py`: **platform records keyed by each platform's own IDs** (`PlayerKey`, `SourcePlayer`, `RosterEntryRecord`, `MatchupRecord`, `TransactionRecord`, `ProjectionRecord`, `InjuryRecord`, `ScheduledGameRecord`, `RulesRecord`). Also recommendations (tip-off guard), takes, trades, evaluations (timeline points), reports (`ReportDocument`), runs, ports. |
| `adapters/` | `_http.py`: `get_raw` with retries, `secret_headers` (sent, never recorded) and list-valued params. `espn/public.py` (no login), `espn/league.py` (cookies), `espn/mapper.py`; the same three for `sleeper/`. `redaction.py`: the fixture redactor and leak checker. **Mappers are pure functions.** |
| `identity/` | `names.py` (normalization), `matching.py` (pure matcher: override → **name + birth date ±1 day (trade-proof)** → name + team → unique name; birth dates are evidence, never a veto), `crosswalk.py` (`sync_players`, `Resolver`; links are stored by platform IDs, so trades never break them; teams refresh every run), `overrides.py` (manual Sleeper → ESPN links; empty so far). ADR 0018. |
| `storage/` | SQLAlchemy 2.1, Alembic `0001` + `0002`, `crypto.py` (AES-GCM bound to the owner), `raw_store.py` (append-only), repositories: users, leagues, credentials, players, league_data, nba, runs. |
| `pipeline/` | `snapshot.py`, `onboarding.py` (`fh league add`), `fixtures.py` (`fh fixtures record`), `ingest.py`. |
| `observability/` | `logs.py` (structlog with secret redaction), `tracing.py` (LangSmith), `alerts.py` (ntfy). |
| `cli/` | `fh version | keygen | doctor | snapshot | league add/list | fixtures record | ingest`. |
| Empty, waiting | `engine`, `facts`, `brain`, `registry`, `reports`, `delivery`, `evaluation`, `api`, `mcp_server` (docstring only). |

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

### Raw store layout
`data/raw/<source>/<dataset>/<YYYY-MM-DD>/<HHMMSSffffff>Z.json.gz` plus a `.meta.json` sidecar. League data lives under the dataset `leagues/<league_id>/<name>`. Never overwrite. It moves to R2 in M3.

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

**Commands:** `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format .`, `uv run mypy` (strict), `uv run lint-imports`, `uv run alembic upgrade head`, `uv run fh doctor`. There are 140 tests: unit, contract (`tests/contract/`) and integration (DB).

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

### M2: Scoring, weekly overview and Sleeper lock-in (Nov 7–12), this repo, `engine/` + `facts/` + `reports/` + `registry/`
The order matters: 1 → 2 → the distributions in 5 → 7 (lock-in), then the rest. Roadmap v0.5 has the step table.
1. **Game logs and migration 0003 (ADRs 0020, 0021).**
   - Add an ESPN per-game actuals dataset (`kona_player_info` with `filterStatsForTopScoringPeriodIds`; entries `statSourceId 0, split 5` carry `externalId` = the ESPN game ID), with the fixture recorded first.
   - Add `player_game_stats`: typed stat columns, a `season`, indexed on `(player_id, game_date)`, linked to `nba_games`, with RLS.
   - In the same migration: convert `projection_rows.per_game` JSONB to one `REAL` column per stat; add composite indexes (`projection_rows (snapshot_id, player_id)`, `projection_snapshots (provider, scope, pulled_at)`, `injury_observations (player_id, provider, observed_at)`, `transactions (league_id, proposed_at)`); add the read-model tables `fact_sheets` and `league_player_values`.
   - Show the database size in `fh doctor` and the ingest summary.
   - Backfill box scores from opening night, then pull them daily.
2. **`engine/scoring.py`:** per-game fantasy points = Σ stat × weight + bonuses (threshold per game). Golden tests: **five players hand-calculated in each points league** (the M2 exit criterion), kept forever in `tests/golden/`. **Cross-check** our scores of real box scores against the per-player points Sleeper and ESPN report in matchups. Done 2026-11-06: scoring matches. Sleeper's weekly points don't reliably reveal locks mid-week (the number changes during the day), so the one-tap button is still needed (see the spike doc).
3. **`engine/schedule.py`:** games per week from `nba_games`; **startable** games under roster slots and daily locks. Sleeper lock-in counts one game per player per week.
   - Open item: map ESPN matchup periods to scoring days. `scheduleSettings.matchupPeriods` and the matchups' `pointsByScoringPeriod` keys both carry this; weeks run Monday to Sunday, and the All-Star break makes a long week.
4. **`engine/valuation.py`:** replacement level = the best free agent at the slot in *this* league. Free agents = players with projections minus today's `roster_entries`.
5. **`engine/distributions.py`** (from game logs, shrunk toward the projection), `streaming.py` (greedy plan within the per-day add rate), `win_probability.py` (normal approximation), `baselines.py`.
6. **`facts/`:** FactSheet v1, held to a token budget by a test, so the nightly report needs zero tool calls (ADR 0020), plus `league_player_values`. **`reports/`:** the weekly `ReportDocument` and the engine-only daily one, rendered locally (CLI to HTML) until M3 delivers them. Categories-league valuation is P8, so the 9-cat league gets games and roster facts only in M2.
7. **Sleeper lock-in, moved from M5 (Adam asked on 2026-11-06; he's playing the league now).**
   - **`engine/lock_in.py`:** backward induction per player. V(last) = E[last game], counted even if he doesn't play (projection 0 when Sleeper's row is empty); V(k) = E[max(Xₖ, V(k+1))]. Lock if last night's score ≥ the value of waiting. Use the closed form for the expected maximum of a normal and a constant.
   - **Sleeper's rules:** one game per player a week; lock after the game and before his next tip; no undo; only starters can lock; if you never lock, his final game of the week counts.
   - **Lock-or-pass cards in the engine-only daily report**, made by `fh run daily`. Adam doesn't want a separate lock-in command. Each card shows last night's score, the value of waiting and the inputs.
   - **The registry's core** (append-only `recommendations` table and the tip-off guard; the domain model already exists), because every call is logged before tip-off.
   - **Version 2 is M5:** decisions across the roster, matchup-aware risk, and an evening ruled-out alert.
- The engine must stay pure: load data in `facts`/`pipeline`, pass plain domain objects into `engine`. Use hypothesis property tests for the math.

### M3: Phone app and delivery (Nov 7–12, parallel), best as its own session and worktree
- **Build:**
  - FastAPI on Google Cloud Run in **`us-east4`**, with one pooled engine per process.
  - Supabase **prod** project in **`us-east-1`**, with Supabase Auth (magic link, invite-only, Adam is user one).
  - PWA (Vite + React + TS + vite-plugin-pwa) on Cloudflare Pages, with Web Push (VAPID).
- **Move the schedule:** snapshot, ingest and the daily run go from launchd to **GitHub Actions** cron, with the raw store on **Cloudflare R2**. Upload the local `data/raw` backlog to R2 first, then uninstall the launchd jobs.
- **Ops:**
  - a **nightly backup** of the tables that can't be rebuilt, to R2 (ADR 0021)
  - ntfy alerts for failed, missing or late runs and for database size
- **Adam's account setup** is a checklist he works through during M2: [runbooks/m3-account-setup.md](runbooks/m3-account-setup.md). Production secrets live in the GitHub environment `prod`: `DATABASE_URL`, `FH_ENCRYPTION_KEY` (**a different key from dev**), `FH_NTFY_TOPIC`, `LANGSMITH_API_KEY` and `FH_R2_*`. VAPID keys are generated in M3.
- **Done when:** a scheduled run, with the laptop closed, pushes the daily report (lock-or-pass cards) and the weekly overview to his phone.

### Open items
- **Q6:** how fast projections react to injuries. Analyze `projection_rows` against `injury_observations` once a week or so of snapshots exists.
- Beta friends and leagues (needed by M7). The RatBall note goes out only if RatBall data is used.
- The 821 unmatched Sleeper players are all non-rostered and **aren't in ESPN Fantasy's player pool**, so there's nothing to link them to. Overrides stay empty until ingest warns about a rostered one. Ingest reports linked pairs with conflicting birth dates (2 known data errors: Gortman, Edey); that's informational.
- ESPN activity-feed trades: P9. Categories-league logic: P8.
- Confirm Adam set the `LANGSMITH_API_KEY` GitHub secret (now step 1 of the M3 setup runbook).
- **Storage levers (ADR 0021),** if the 60% size alert fires: store only changed projection rows, and store only roster changes. Season rollover to the R2 archive: P11.
- **Before M7:** friend invites need a custom email sender for Supabase Auth (the built-in one only reaches organization members, 2 emails an hour), which needs a domain Adam owns.

## 8. Session history (for orientation)

- **2026-10-16:** read the 25-page spec; architecture, repo structure and roadmap drafted.
- **2026-10-19:** Adam's review. User and league data go in the database behind auth (not `.env`); timeline graphs for takes, calls and trades added; M3 split; Supabase from day one; decisions D1–D10.
- **2026-10-25 to 11-06:** M0 built. Supabase dev, LangSmith and ntfy set up together (pooler URL, iPhone notification fix); PR #1 audited for leaks and merged.
- **2026-11-06:** M1. Snapshots started and scheduled; `fh league add` for three leagues; ESPN and Sleeper explored; the redacting recorder; mappers; identity; tables; `fh ingest` run live twice; ADRs 0016–0019; roadmap v0.4; this handoff doc. PR #2 merged.
- **2026-11-06 (pre-M2 review):** Adam asked for low latency for agents and a future chat, and for room to keep history for future years. Latency was measured against Supabase dev, and one and five synthetic seasons were benchmarked locally. Result: ADR 0020 (read models, few batched tools, in-process MCP, one region) and ADR 0021 (typed projection columns, a size alert, the season archive to R2, nightly backups). Sleeper lock-in moved to M2, as cards in the daily report. The M3 account-setup runbook was written for Adam.
