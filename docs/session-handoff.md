# Session handoff: everything a new session needs

**Last updated:** 2026-11-06, during M3 (branch `m3-phone-app`): part 1, cloud snapshots, is built. M2 was merged as PR #3. **Now: M3** (phone app and delivery), in five parts (§7).
**Read this first**, then `CLAUDE.md` (rules), `docs/roadmap.md` (milestones), `docs/architecture.md` (design) and `docs/decisions/` (ADRs). This file is the narrative glue: what exists, how we work, what we learned the hard way, and exactly what comes next.

The repo is **public**, so this file holds no secrets or personal data. League names and IDs, the owner's email, the Supabase project ID and the ntfy topic live in `.env` and the database. `uv run fh league list` shows the leagues.

---

## 1. The project in one paragraph

A fantasy basketball assistant for Adam (customer one), then a friends beta, then a paid launch for 2027-28. Every morning it sends a short briefing to his phone, with specific moves tuned to each league's own scoring, built on his own takes (a watchlist of beliefs), and it **grades every call it makes** against a baseline, all season. It doubles as a portfolio case study (deployment strategist; "the Blitzy pattern scaled down": an orchestrator with specialized agents and validation). The source spec is *Fantasy Basketball Helper: Product Spec* (2026-10-15, 25 pages; it lives outside the repo).

**Core principles** (never broken; details in CLAUDE.md):
- **Code computes every number; models only argue.** The engine makes candidate moves with expected values; LLMs read a FactSheet, select, explain and challenge; guardrails reject ungrounded numbers.
- **Evidence over opinion.** Every call is logged before tip-off and never edited; inputs are versioned snapshots; grades come later, against a baseline.
- **Adapters isolate platforms.** ESPN's API is unofficial; when it changes, only `adapters/espn/` changes.

## 2. Where things stand (2026-11-07)

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
| M3 Phone app + delivery | **In progress** (Nov 7–12). **Part 1 merged** (PR #4, 2026-11-07): cloud snapshots on R2 (ADR 0023), live since 10:56 ET that day; the cutover is under way, with the laptop as an independent backup until part 5 ([runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md)). **Dependabot merged** (PR #5, ADR 0024): updates merge themselves once CI passes. Adam's accounts: sections 1–4 done, 5–6 not started ([runbooks/m3-account-setup.md](runbooks/m3-account-setup.md)); everything that exists: [runbooks/accounts-and-secrets.md](runbooks/accounts-and-secrets.md). |
| M4–M8, P9–P12 | See `docs/roadmap.md` v0.5. Friends beta **Nov 29**, hard stop **Dec 15**. |

### What runs today
- **`fh snapshot`** runs on Adam's Mac through launchd: the morning set at **06:30** and the pre-tip evening set at **17:30** local time (America/New_York). It saves raw ESPN and Sleeper public data to `data/raw/` (git-ignored). History has accumulated since **2026-11-06**. Check it with `scripts/snapshot-schedule.sh status`; logs are in `~/Library/Logs/fantasy-helper/` (JSON lines). A sleeping Mac runs missed jobs on wake; a Mac that's shut down skips them. **The cloud job is live** since 2026-11-07 10:56 ET (`.github/workflows/snapshot.yml`, 06:17 and 17:17 Eastern, writing to R2; first run 17:17 that day). The laptop keeps saving to its own disk, independently, until four clean cloud runs **and** the Healthchecks watchdog (part 5); then launchd is removed (ADR 0023, Update 2026-11-07). The backlog (1,716 files on 2026-11-07) isn't in R2 yet: cutover step 2.
- **`fh ingest`** is run by hand for now. It loads every not-yet-loaded snapshot plus fresh league data into Supabase, takes about 30 seconds, and is idempotent. First live run: 1,100 ESPN players; **all 1,852 active Sleeper players** (the Sleeper league has 142 rostered, all linked to ESPN, and **1,710 free agents**, 889 linked to ESPN and 410 with Sleeper projections); 1,200 games; 9 projection snapshots (8,490 rows); 226 injury changes; full rosters, matchups and transactions for all three leagues.
- **`fh run daily` and `fh run weekly`** are run by hand (M3 schedules them). Each runs ingest first, which now also loads box scores and ESPN's lineup for each finished day. They write local HTML pages (`--open` opens them) and log every call in the registry. `fh run daily --dry-run` saves nothing.
- **Supabase dev project**, migrated to **0005**. The **prod project** exists since 2026-11-07 and its `DATABASE_URL` is in GitHub's `prod` environment; it isn't migrated yet (part 2).
- **LangSmith tracing** works (`fh doctor` sends a smoke trace). **ntfy run events** reach Adam's phone: every run sends started, steps and success quietly and failures loudly, and it's ops only; product notifications come from the PWA ([runbooks/run-events.md](runbooks/run-events.md), added 2026-11-06). iPhone notifications needed a settings fix, which is done.
- **GitHub:** `adamhrogers/fantasy-helper`, public. CI runs lint, mypy, contracts, tests on Postgres 17, `alembic check`, and a gitleaks full-history scan. The repository secret `LANGSMITH_API_KEY` is set, and CI's smoke trace passes on `main` (checked 2026-11-06). Since 2026-11-07: the `prod` environment (only `main`; 7 of 8 secrets), the `main` ruleset (both CI jobs required, admin bypass), and Dependabot with auto-merge. Every action is pinned to a commit.

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
- **Commit footer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **PR body footer:** `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. After opening a PR, use the app's PR tools (get_status / bind) and offer Auto-fix; Adam turned it on for PR #1 and PR #3.
- **Secrets never pass through chat.**
  - Keys are generated straight into `.env` without being printed.
  - ESPN cookies are typed at `fh league add`'s hidden prompt.
  - GitHub secrets are set by Adam with `gh secret set`.
  - Scripts that touch real league data print **structure and counts only**, never member names.
- **Style he likes:** plain language, concise, step-by-step for anything he has to do by hand, with commands in their own fenced `bash` blocks. He asks "what is X?" questions; answer them directly. He isn't attached to specific tools and accepts reasoned recommendations.
- **He wants the reasoning and the honest state:** what was verified, what wasn't, and false alarms called false alarms. He specifically asked for a leak audit before merging M0. It was clean: `.env` never committed, none of his secret values in history or the PR, gitleaks and GitHub secret scanning clean.
- **Code reviews go on GitHub** (Adam, PR #3): post the findings as inline review comments on the PR, fix them all, reply on each comment with the fixing commit and its test, then resolve the threads. He merges once the threads are resolved and CI is green.
- **Live data has context** (Adam, 2026-11-06): reason with dates and the matchup week as a whole; games in progress today aren't final. Don't fit a rule to data that isn't consistent; say it doesn't fit (that's how lock detection got parked).
- **Parallelism:** sub-agents and parallel sessions are fine once a plan is approved. M2 and M3 are the natural pair to split (they share no files).

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

**Commands:** `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format .`, `uv run mypy` (strict), `uv run lint-imports`, `uv run alembic upgrade head`, `uv run fh doctor`. There are 257 tests: unit, golden (`tests/golden/`, the hand-checked scores), contract (`tests/contract/`) and integration (DB).

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

### Found on 2026-11-07
- **The laptop ran M1 code. Fixed:** its main checkout is at `02c3f0f` (M3 part 1) since 2026-11-07. After every merge, `git pull` there: launchd runs whatever that checkout holds.
- **Sleeper re-issued its season projections** (06:30 run, 2026-11-07): 1,334 rows that held only ADP fields went empty, `gp`, `dd`, `td` and `dreb` were dropped, minutes became realistic, and 564 players have a points projection (524 the day before). Still per-game averages. The old check (half of all rows must have stats) flagged it; it now requires 300+ players with a points projection, like the ESPN check. None of Adam's leagues score the dropped stats.

### Left from M2 (none blocks M3)
- **ESPN matchup periods:** map `scheduleSettings.matchupPeriods` to scoring days **before the All-Star break** (February). Until then a period that started before this calendar week is flagged (`period_mismatch`), with no banked points or win probability.
- **Mid-week win probability for lock-in leagues:** needs lock data. Lock detection is parked (Adam, 2026-11-06); the plan is the one-tap "I locked / I passed" button in the PWA (lock-in v2, M5).
- **Categories-league valuation:** P9. The 9-cat league gets games and roster facts only.
- **Snapshot days are permanent:** keep launchd running until the cloud has four clean runs **and** the part 5 watchdog is live.

### M3: Phone app and delivery (Nov 7–12), branch `m3-phone-app`
Approved by Adam on 2026-11-06, in five parts, each a checkpoint he approves:

| Part | Builds | Needs from Adam | Status |
|---|---|---|---|
| **1. Snapshots to the cloud** | `R2RawStore` (`storage/r2_store.py`), `fh raw upload`, `fh doctor --only raw`, `.github/workflows/snapshot.yml` with the DST gate (`pipeline/cron.py`), once-a-day guard for Sleeper's player list, crash alerts. ADR 0023 | Setup sections 1, 3 and 4 (**done**); then [runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md) | **Merged** (PR #4, 2026-11-07), **live**. Cutover: step 1 done; backlog upload, first run (17:17 ET) and the overlap to go |
| **2. Prod database and scheduled runs** | Prod migrated, Adam's user and leagues in prod (local git-ignored prod env file), full history ingested from R2, migration 0006 (`reports`, `push_subscriptions`), `daily.yml` and `weekly.yml`, run log in the snapshot job | Sections 2 and 3 (**done**); ESPN cookies typed once at the hidden prompt | Next |
| **3. API on Cloud Run** | FastAPI (reports, push subscriptions), Supabase JWT checked against its public keys (JWKS), every query scoped to the user, Docker image, keyless deploy (Workload Identity Federation), Secret Manager, max 2 instances | Section 5 (**not started**), plus the WIF commands | |
| **4. The phone app** | `web/` PWA: sign-in, Today and This Week tabs, Home Screen install, Enable notifications, types from OpenAPI, Cloudflare Pages | Connect Pages to GitHub; the iPhone | |
| **5. Push, ops, exit test** | VAPID keys straight into secrets, "report ready" push, Healthchecks pings (missed run, 7:45 late report), size alert, nightly backup and restore drill, broken-run test, laptop-closed test | Section 6 (**not started**); check schedules | |

**Decided with Adam (2026-11-06):**
- **Sign-in by 6-digit email code, not a magic link.** A Home Screen app on iOS doesn't share Safari's session, so a tapped link would sign in Safari, not the app. Same Supabase Auth; the email template shows `{{ .Token }}`.
- **The prod registry starts empty at go-live.** Dev's calls since 2026-11-06 stay in dev as test data. All snapshot history loads into prod from R2.

**How M3 merges:** GitHub runs scheduled workflows only from `main`, and `prod` is limited to `main`, so each part that needs the cloud merges as its own PR (part 1 first), rather than one PR at the end.

**Done when:** a scheduled run, with the laptop closed, pushes the daily report (lock-or-pass cards) and the weekly overview to his phone, and they open after sign-in; a deliberately broken run produces an ops alert; a backup restores into a scratch database.

### Dependency updates (ADR 0024)
Dependabot opens grouped PRs (security fixes as they land; version updates Mondays 07:00 Eastern, 7-day cooldown, no Python majors), and `dependabot-auto-merge.yml` merges each one once CI passes. It refuses unless `main`'s ruleset requires both CI jobs ([runbooks/dependency-updates.md](runbooks/dependency-updates.md)); the ruleset `main` was created on 2026-11-07, with an admin bypass. **First run, 2026-11-07 (checked):** within five minutes of the merge, Dependabot opened PRs #6 (setup-uv 7.6.0 → 10.3.3), #7 (six Python updates) and #8 (the `uv_build` range), and each merged itself only after both CI jobs passed. GitHub doesn't rerun CI on `main` after these merges (ADR 0024), so the combined `main` was checked by hand: lockfile consistent, all checks and 310 tests pass. **Still to see:** the first security PR, whose job log should show the major-version `ignore` doesn't apply to security updates.

### Tabled from the PR #4 review (2026-11-07)
- **4, shared crash guard:** `alert_crash` and `REPORTED_EXIT` exist and the snapshot uses them. `fh ingest`, `fh run daily` and `fh run weekly` adopt them in **M3 part 2**, when they get workflows.
- **8 and 9, a reused once-a-day result** hides a flagged payload and drops freshness. Revisit in **part 5**, with the stale-source check.
- **6, R2's `BadDigest` is treated as permanent.** Revisit if one shows up in the run log, or with other R2 hardening in part 5.
- **7, a 409 `ConditionalRequestConflict` reads as "already exists".** Each key has one writer; revisit if two writers can ever target the same key.
- **10 and 12, `fh raw upload` efficiency** (re-HEADs listed objects; orphans checked against a list). Fold into the next change to `raw_upload.py`.

### Open items
- **Security, R2:** each machine gets its own R2 key at the end of the cutover (step 5; the laptop's needs write, because ingest saves league responses). Part 5 adds a bucket lock on the **public** dataset prefixes only: league data must stay deletable for a beta friend who leaves (ADR 0023, Update 2026-11-07).
- **Not verified by Claude:** prod Supabase's region, plan and the sign-ups-off setting (Adam did section 2). Part 2 checks them when it migrates prod.
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
