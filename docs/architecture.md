# Fantasy Helper — Technical Architecture

**Status:** Draft v0.3 · **Date:** 2026-11-06 (v0.2: 2026-10-19) · **Source:** *Fantasy Basketball Helper: Product Spec* (Oct 15, 2026, 25 pp.)

**Changes in v0.3 (2026-11-06):** a read path built for low latency: pre-computed read models, few batched tools, in-process MCP, one region (§5.4, ADR 0020). A storage budget that keeps Postgres inside the free tier, with every season archived to R2 for future years and nightly backups of the irreplaceable tables (§5.5, ADR 0021). Sleeper lock-in moves to M2, inside an engine-only daily report (roadmap v0.5).

**Changes in v0.2:** user and league data live in the database from M1, behind auth from M3 (§12.1). Supabase replaces SQLite from day one (§11). Takes, calls and trades get timeline graphs (§8.1). The decisions are resolved (§15).

This document turns the product spec into a buildable system design. It keeps every decision the spec already made, fills in the gaps the spec leaves open, and flags the few places where I recommend a change.

---

## 1. Architectural drivers

These are ranked. When two of them pull in different directions, the higher one wins.

| # | Driver | Where it comes from in the spec | What it forces in the design |
|---|---|---|---|
| 1 | **Evidence over opinion** | Grading rules, the recommendation registry, "same snapshot" | Inputs are stored append-only and versioned. Calls are frozen before tip-off. Grading reads the exact inputs each call saw. |
| 2 | **Code computes, models argue** | The brain, working rules | A hard boundary between the deterministic engine and the LLM brain, enforced by checks rather than left to convention |
| 3 | **Platform independence** | Data and platforms, the 6-layer diagram | Ports and adapters. Nothing above the adapter layer knows which platform a league lives on. |
| 4 | **Deadline: friends beta by Nov 15** (Dec 15 at the latest) | Roadmap | Boring technology, one backend language, simplest hosting that works, no speculative abstractions |
| 5 | **Observable from the first commit** | Observability | Tracing, a run log, alerts and preflight checks are part of the skeleton, not added later |
| 6 | **Portfolio story** | Portfolio goal | A decision log (ADRs), metric snapshots and trace artifacts are kept as first-class repo content |
| 7 | **About $0 of infrastructure** | Hosting | Free tiers only. The only real spend is LLM tokens, with a Console spend limit. |

---

## 2. System context

```
                    ┌──────────────────────────── External ────────────────────────────┐
                    │ ESPN (unofficial, cookies)   Sleeper (official, read-only)         │
                    │ Yahoo (OAuth, offseason)     Projection/news sources (free-first)  │
                    │ Anthropic API   2nd-family LLM (Skeptic, M8)   LangSmith           │
                    │ Browser push services (APNs/FCM via Web Push)   Ops alert channel  │
                    └───────────────────────────────▲──────────────────────────────────┘
                                                    │
  Adam / beta friends ──(PWA on phone)──►  Fantasy Helper  ◄──(cron)── Scheduler
```

---

## 3. Layered architecture

The spec defines six layers. I keep them and add four planes that cut across all of them.

```
 ┌──────────────────────────────────────────────────────────────────────────────────────┐
 │ L6  DELIVERY      PWA (web/)  ◄─HTTPS─►  App API (FastAPI)   Web Push   Ops alerts      │
 ├──────────────────────────────────────────────────────────────────────────────────────┤
 │ L5  BRAIN         LangGraph graphs: baseline │ council (Analyst·Scout·Voice·Skeptic·    │
 │                   Editor) → guardrails → ReportDraft      tools via ONE MCP server     │
 ├──────────────────────────────────────────────────────────────────────────────────────┤
 │      FACTS        FactSheet builder: compact, ID'd, code-computed facts + candidate    │
 │                   moves. This is the only thing models read by default.                │
 ├──────────────────────────────────────────────────────────────────────────────────────┤
 │ L4  ENGINE        scoring · valuation · schedule · distributions · streaming ·          │
 │                   win probability · lock-in · belief signals · baselines   (pure code) │
 ├──────────────────────────────────────────────────────────────────────────────────────┤
 │ L3  DATA MODEL    domain (Pydantic) + storage (SQLAlchemy/Alembic) + player identity   │
 │                   zones: raw (immutable payloads) → core (normalized, versioned) →     │
 │                   derived (fact sheets, reports, registry, grades)                     │
 ├──────────────────────────────────────────────────────────────────────────────────────┤
 │ L2  ADAPTERS      espn │ sleeper │ yahoo (later) │ projections │ news │ stats          │
 │                   each = client (I/O) + recorder (fixtures) + mapper (raw → domain)    │
 ├──────────────────────────────────────────────────────────────────────────────────────┤
 │ L1  SOURCES       ESPN · Sleeper · Yahoo · projection/news providers                   │
 └──────────────────────────────────────────────────────────────────────────────────────┘
   Cross-cutting planes:  PIPELINE (job orchestration)  │  REGISTRY + EVALUATION (calls, grading)
                          OBSERVABILITY (LangSmith, run log, alerts)  │  CONFIG + SECRETS
```

**Dependency rule:** a layer may import only from layers below it. `domain` imports nothing internal. CI enforces this with **import-linter** contracts, which turns the spec's principles into checks that fail the build:

| Contract | What it guarantees |
|---|---|
| `domain` imports no other internal module | The data model stays pure |
| `engine` cannot import `storage`, `adapters` or `brain` | The engine is pure math and stays testable and replayable |
| `brain` cannot import `storage` or `adapters` | Models only ever see FactSheets and MCP tools. This is how "models never invent a stat" is enforced. |
| Nothing below `pipeline` imports `pipeline`, `api`, `cli` or `mcp_server` | Interfaces stay at the edges |

**Shape:** hexagonal (ports and adapters). `domain/ports.py` defines Protocols (`LeaguePlatform`, `ProjectionSource`, `NewsSource`, `StatsSource`, `Notifier`, `Clock`). Platform adapters implement them. CLI, MCP and HTTP are three thin interfaces over the same services.

---

## 4. Modules

All of these live in one Python package, `src/fantasy_helper/`, with strict internal boundaries. One package with linted boundaries suits a solo builder on a four-week clock. Splitting into a multi-package uv workspace later is mechanical if it's ever needed.

| Module | Responsibility | Notes |
|---|---|---|
| `config` | Typed settings (pydantic-settings), role→model map, feature flags | Holds **only the system's own secrets and settings** (§12.1). League IDs, platform credentials and anything per-user live in the database. The app's key is **`FH_ANTHROPIC_API_KEY`**, never `ANTHROPIC_API_KEY`, so it can't leak into the Claude Code shell. |
| `domain` | Pydantic v2 models, IDs, enums, value objects, ports | Zero I/O |
| `adapters` | One package per source: `client` (HTTP, retries, rate limits), `recorder` (captures and redacts fixtures), `mapper` (raw → domain) | ESPN is called directly over httpx, with `espn-api` as a reference (ADR 0016). Mappers are pure functions, tested against recorded fixtures. A validation failure means schema drift and raises an alert. |
| `identity` | Canonical player registry and cross-platform ID crosswalk (ESPN ↔ Sleeper ↔ provider IDs ↔ normalized names) | **Gap in the spec.** Sleeper has no ESPN IDs for NBA players, so links are made by normalized name and team, conservatively, with manual overrides (ADR 0018). |
| `storage` | SQLAlchemy 2.0 tables, repositories, Alembic migrations, raw payload store | Supabase Postgres from M0. CI tests against a Postgres service container. |
| `engine` | Deterministic math (see §6) | Pure functions. Each result carries provenance (the snapshot and settings IDs it used). |
| `facts` | Builds one **FactSheet** per league per run: compact facts with IDs, plus engine-generated **candidate moves** with EV and baseline | Stored and hashed. **It's the replay unit for regression evals and shadow runs.** |
| `brain` | LangGraph graphs (`baseline`, `council`), agents, versioned prompts, structured output schemas, guardrails, LLM gateway | See §7 |
| `mcp_server` | One FastMCP server over our data model, **read-only** tools | Our own agents (pipeline, chat API) call it **in-process** (in-memory transport); stdio or streamable HTTP only for outside clients (ADR 0020) |
| `registry` | Append-only recommendation log, tip-off guard, follow detection | See §8 |
| `evaluation` | Grader, outcomes, baselines comparison, metrics, report cards, slicing, miss causes, **trajectories**, replay (regression and shadow) | See §8 |
| `reports` | Builds the `ReportDocument` card model for daily, weekly and scorecard views | Rendering contract shared with the PWA |
| `delivery` | Publishes reports and sends Web Push (VAPID) | Product push only. Ops alerts live in `observability`, on a separate channel. |
| `pipeline` | Daily, weekly and grade jobs, preflight checks, run log | Plain Python stages, **not** LangGraph. LangGraph is used only inside the brain. |
| `observability` | structlog JSON logs (secrets redacted), LangSmith setup and tags, token and cost accounting, **ops alerts** (ntfy) | Every LLM call is tagged with run_id, league, agent_setup and prompt_version. Alerts sit low in the layers, so any module can raise one. |
| `api` | FastAPI backend-for-frontend for the PWA: auth, reports, watchlist and takes, feedback, push subscriptions, scorecard | **Gap in the spec:** a static PWA needs a write path and auth (§9) |
| `cli` | Typer entrypoint `fh`: `league add`, `ingest`, `run daily/weekly`, `grade`, `fixtures record`, `mcp serve`, `replay` | Operator tool. `league add` writes a league and its encrypted credentials to the database (M1). The PWA onboarding screen replaces it for friends (M7). |
| `web/` | PWA: Vite + React + TypeScript + vite-plugin-pwa | API types are generated from FastAPI's OpenAPI, so one contract serves both sides |

---

## 5. Data architecture

### 5.1 Three zones

| Zone | What it holds | Where it lives | Rule |
|---|---|---|---|
| **raw** | Every source response as received (gzipped JSON), keyed by source, endpoint, league and `pulled_at` | Local disk now; **Cloudflare R2** (10 GB free) from M3, **kept forever** | Immutable. Kept **out of Postgres**, because ESPN payloads would use up a 500 MB free tier in weeks. |
| **core** | Normalized domain data | Supabase Postgres from M0 (separate dev and prod projects), **current season only** | Versioned where grading depends on it. Past seasons live in the archive (§5.5). |
| **derived** | FactSheets, reports, recommendations, grades, report cards, run log, **read models** (§5.4) | Same database | Recommendations are insert-only. Grades go in a separate table. Read models are rebuilt each run. |
| **archive** | Each finished season's tables as Parquet, plus nightly backups of the tables that can't be rebuilt from raw | Cloudflare R2 | Written once per season (archive) or nightly (backups). Read with DuckDB, no database needed. |

### 5.2 Versioning rules (from "Same snapshot")

- **Projections:** immutable `projection_snapshots(id, source, pulled_at, scope)` plus per-player, per-stat rows. Nothing is ever updated in place.
- **League settings:** `league_settings_versions`. A new version is written whenever the hash of the scoring weights, roster slots, acquisition limits or lock rules changes.
- **Takes:** immutable. An edit creates a new take with `supersedes_id`, and both get graded.
- **Recommendations:** insert-only. `created_at` must be earlier than the first relevant tip-off (the tip-off guard). Grades live in `grades`, keyed by `recommendation_id`.
- **Time:** everything is stored in UTC, with tip times per game. Display uses each user's time zone.
- **Tenancy:** every user-scoped row carries `user_id` **from day one**. That costs nothing now and avoids a migration at the beta.

### 5.3 Core entities

| Group | Entities |
|---|---|
| Identity and tenancy | `users`, `platform_credentials` (one per user and platform, **encrypted**), `push_subscriptions` |
| League | `leagues` (platform, format: points / categories / lock-in), `league_settings_versions`, `teams`, `roster_snapshots`, `transactions`, `matchups` |
| NBA | `players` (canonical), `player_external_ids`, `nba_games` (schedule, tip times), `player_game_stats` (box scores) |
| Inputs | `projection_snapshots`, `projection_rows`, `news_items` |
| Judgment | `watchlist_entries`, `takes` (player, claim text, **direction**, conviction 1–5, horizon, created_at) |
| Brain | `runs` (the run log), `fact_sheets`, `league_player_values` (read model, §5.4), `agent_setups`, `reports` |
| Evidence | `recommendations` (every field from the spec's registry table, plus `is_shadow`, `first_tip_utc`, `fact_sheet_id`), `recommendation_follows`, `grades` (outcome, metric, baseline, lift, grade, miss cause and its source), **`evaluation_points`** (one per open take, call or trade per night; §8.1), `report_cards`, `feedback` |

**Gradable takes (refinement):** a free-text take can't be graded mechanically. The phone form asks for a **direction** (will beat or trail projections), a **horizon** (7, 14 or 30 days) and conviction, and keeps the one-line text for color. Code can then grade every take with no LLM.

### 5.4 Read path and latency (ADR 0020)

There are two kinds of reads, and they're stored differently:

| | Who reads | What | Where | Rule |
|---|---|---|---|---|
| **Hot** | Agents, MCP tools, the phone app, chat later | Today's facts for one league | `fact_sheets` (append-only), `league_player_values` (replaced each run), latest snapshot rows | Pre-computed by code. One indexed lookup per tool call. Small enough to stay in memory however much history piles up. |
| **History** | Grading, trajectories, replays, season analysis | Every snapshot and box score | `projection_rows`, `roster_entries`, `player_game_stats`, then the archive | Append-only, read in batch at night. Indexed for "as of pull X" and "one player over time". |

**Measured 2026-11-06:**
- Supabase dev from Adam's Mac: 0.3–2.7 ms on the server per hot query, about 34 ms per round trip, about 600 ms per new connection.
- A synthetic full season on a local Postgres 17 (720,000 projection rows): every hot query stayed under 5 ms.
- At five seasons (3.6 million rows), two queries reached about 25 ms until composite indexes such as `(snapshot_id, player_id)` brought them back down.

**What dominates latency, biggest first:** model round trips (about 1–3 s per tool call), cold starts, new connections, then the network (about 1–5 ms when the server and database share a region). The query itself is the smallest cost. So:
- Agents get **few, coarse, batched tools**, and the FactSheet answers the nightly report with **zero** tool calls, held to a token budget by a test.
- The API keeps a connection pool open and runs in the database's region: Supabase `us-east-1` and Cloud Run `us-east4`, both in Northern Virginia.
- No cache tier, vector database or second database.

### 5.5 Storage budget and history (ADR 0021)

**Supabase Free, confirmed 2026-11-06:**
- 500 MB database size per project. Going over is a billing-quota breach that brings Fair Use restrictions.
- 2 GB fixed disk, read-only at 95%.
- 500 MB RAM.
- Two active projects.
- Pauses after a week with no activity.
- **No automatic backups.**

**What fits:**
- Measured at the real snapshot cadence, projections alone would reach about 275 MB by mid-April as JSONB, and the whole database 350–400 MB.
- With **one `REAL` column per stat** (measured 173 bytes a row against about 380), projections take about 125 MB a season and the database ends the season around 200–250 MB, even with about 10 beta leagues.
- A daily size check alerts at 60% and 80%.
- The next levers, if needed, are storing only changed projection rows and only roster changes.

**History for future years:** nothing is ever lost.
- Raw payloads stay in R2 forever, about 0.3 GB a season.
- After each season (P11, then every April), its tables are exported to Parquet in R2 (`archive/season=YYYY/<table>.parquet`, about 13 MB for a season of projections). They stay queryable with DuckDB for multi-year work: projection accuracy by provider, aging curves, our own call history. The season's bulky rows then leave Postgres. Small, high-value tables (players and the crosswalk, game logs, registry, takes, grades) stay.
- Every season-scoped table carries a season, directly or through its league.

**Backups:** a nightly job (M3) copies the tables that can't be rebuilt from raw (users, leagues, credential ciphertext, takes, recommendations, grades, feedback, FactSheets, reports) to R2. Everything else can be rebuilt with `fh ingest`.

---

## 6. Engine (code computes)

| Component | Approach (v1) | Feeds |
|---|---|---|
| `scoring` | Per-stat projection × league weights from `league_settings_versions` | Everything downstream |
| `valuation` | Replacement level = best free agent at the same slot in *this* league. League-relative z-scores for categories. | Streaming, signals, categories |
| `schedule` | Games per week, plus **startable** games under roster slots and lineup locks | Weekly overview, streaming |
| `distributions` | Per-player, per-game mean and variance in league scoring, from game logs (shrunk toward the projection) | Lock-in, win probability |
| `streaming` | Greedy day-by-day add/drop plan within acquisition limits. An ILP (OR-Tools) only if greedy leaves value on the table. | Weekly overview, daily moves |
| `win_probability` | Normal approximation over the sum of startable-game distributions. Monte Carlo when categories arrive. | Matchup outlook |
| `lock_in` | Optimal stopping by backward induction: V(last) = E[X]; V(k) = E[max(Xₖ, V(k+1))]. Lock tonight's score if it ≥ V(remaining). | Sleeper lock/pass cards |
| `signals` | Watchlist actual vs projected over rolling windows, crossed with take direction → pick up / buy low / your take is wrong | Daily report |
| `baselines` | Baseline call per call type, computed at call time (top free agent by projection, lock above projection, and so on) | Registry and grading |

Each engine output has a golden test. The M2 exit criterion ("five players match a hand calculation in each league") becomes a permanent test suite.

---

## 7. Brain (models argue)

### 7.1 Engine proposes, brain disposes

The engine generates **candidate moves with EVs and baselines**. The brain selects, ranks, explains and challenges them, checks them against your takes and assigns a confidence bucket. If an agent wants a move that isn't in the candidate set, it calls the MCP tool `evaluate_move(add, drop, day)`, and **code** scores it. No number reaches the registry unless code produced it.

### 7.2 Graphs

```
baseline (M4):   load FactSheet → writer (one Claude model) → guardrails ─┬─► ReportDraft
                                                     ▲                 └─ violation → retry once → fail closed
council  (M8):   load FactSheet → ┌ Analyst ┐
                                  ├ Scout   ├→ Skeptic → Editor → guardrails → ReportDraft
                                  └ Voice   ┘
```

- **One output contract:** both graphs emit the same `ReportDraft` schema. Registry, grading and shadow runs then compare like with like, and agents can be added one at a time (spec build order).
- **Agent setups are versioned config** (`evals/setups/*.yaml`): graph, role→model map and prompt versions. The setup ID goes into `producer` on every call.
- **Guardrails:** (1) schema validation; (2) **numeric grounding**: every number in the prose must exist in the FactSheet or come from a tool result; (3) candidate-set check; (4) no stat claims about players missing from the FactSheet.
- **Graceful degradation:** if the brain fails after retry, the report still ships as the **engine-only** version (the M2 "no AI" overview format), marked as such. The run log records it.

### 7.3 LLM gateway and models

- **Role→model map in config.** The spec leaves the final assignment open. Starting point from the spec: **Claude Haiku 5.5** (`claude-haiku-5-5`, $0.10/$0.50 per MTok) for routine agents, and **Claude Sonnet 5.5** (`claude-sonnet-5-5`, $2/$10) for Editor and baseline. The Skeptic comes from a **different model family** (a second provider key, needed by M8).
- **Structured outputs:** use native structured outputs (`output_config.format`), **not forced tool calls**. Current Sonnet and Opus models return a 400 on forced `tool_choice`. **M0 checks** that the installed `langchain-anthropic` structured-output method uses native JSON schema.
- **Refusals and errors:** always check `stop_reason`. Retryable and non-retryable errors are handled separately, and a failure falls through to graceful degradation.
- **Prompt caching:** stable content (system prompt, scoring rubric, output schema) goes first with a cache breakpoint, and the volatile FactSheet goes after it. Shared prefixes must exceed the model's minimum cacheable length to cache at all. Check `cache_read_input_tokens` in LangSmith. **In the council (M8),** shared rules and the FactSheet come before each role's instructions, and agents share one tool list. Agents on the same model then reuse one cached FactSheet, so they answer faster and cost less (ADR 0020).
- **Batch API: recommend deferring** (a departure from the spec). At this scale a Sonnet daily report costs roughly $0.05–0.10 per league, so Batch would save cents per day. The costs are real, though: an asynchronous window that fights the 8 a.m. deadline, debate rounds that each become a batch stage, and LLM calls that LangSmith no longer traces automatically. The gateway exposes an executor interface so Batch can be added in one place once cost data justifies it.

### 7.4 MCP server (one, read-only)

Tools are **few, coarse and batched**, because every tool call costs a model round trip (ADR 0020):
- `search_players(name)` resolves names to IDs.
- `get_player_cards([ids], league)` returns the projection, recent form, injury status, league value and roster status in one call.
- `get_league_context(league)`
- `get_free_agents(league, slot, limit)`
- `get_takes`, `get_news`, `get_call_history`
- `evaluate_move`

Each reads the read models (§5.4) with one query and returns compact, ID'd JSON with a size cap. LangGraph agents reach them through `langchain-mcp-adapters`, in-process. **There are no write tools in v1**: the app never acts on a league, and writes to the registry are done by pipeline code after guardrails, not by agents. That keeps "log before outcome" deterministic and limits the damage from prompt injection in news text.

---

## 8. Registry and evaluation

```
Recommend ─► Log (frozen, tip-off guard) ─► Wait (window) ─► Grade vs baseline ─► Report card ─► Improve ─┐
    ▲                                                                                                       │
    └──────────────────────────────── replay stored FactSheets (regression / shadow) ◄─────────────────────┘
```

- **Follow detection:** from league transactions (adds and drops). For lock actions, if the Sleeper API doesn't expose them (a **spike question**), the PWA card gets a one-tap "I locked / I passed" confirmation.
- **Metrics:** as specified per call type (points lift, Brier score with calibration buckets, MAE, categories won, share of takes backed by the data). **Bootstrap 95% CI** for an A grade. Cells with fewer than 20 graded calls show as Incomplete.
- **Miss causes:** rules first (projection error = projection vs actual gap; data error = news timestamp after the pull), then an LLM-judge suggestion for reasoning vs variance, with a human confirming in the monthly deep dive. The source of each label is stored.
- **Slicing:** by platform, format, call type, confidence, user and agent setup. The weekly card names the top and bottom three slices that have enough calls to grade.
- **Regression evals:** graded days are exported as a LangSmith dataset. A CI job (`evals.yml`) runs when `brain/` or `prompts/` change, replays stored FactSheets through the new setup, and fails if lift drops.
- **Shadow runs:** a candidate setup runs on the same FactSheet with `is_shadow=true`. It's graded but never shown.

### 8.1 Trajectories: how a decision looks over time

The final grade answers *how did it turn out*. The trajectory answers *how did it look along the way*. Each night, every open take, call and trade gets a new point until its window closes:

```
expected value on day d  =  realized so far       (actual box scores, in league scoring)
                         +  projected remaining   (that day's projections × remaining startable games)
```

```
 Example: you trade for an injured player who's expected back in 2 weeks
 points vs. what you gave up
  +20 ┤ ●──●──●──●╮                                  ┊
    0 ┼───────────┼──────────────────────────────────┊──── baseline: no trade
  -20 ┤           ╰●──●──●──●──●──●──●──●──●──●──●   ┊
      └─┬─────────▲───────────────────────────────────┊─
      trade    re-injured,                      window ends =
               out ~2 months                    final grade
```

- **News moves the curve the next morning.** A re-injury collapses projected remaining. **Performance moves it as games are played.** At the window's end projected remaining is zero, so the curve lands exactly on the final grade and the graph can never disagree with the grade.
- **Takes** plot your direction × (expected total − what the projection expected when you made the take). Above zero means the take is looking right. A take that looked wrong at first and then came good shows up as a curve that crosses zero.
- **Trades** plot each side's value over replacement in league scoring, for both teams, over the spec's short-term window (first two weeks) and long-term window (rest of season). This covers every trade in the league, not just yours.
- **Calls** (add/drop, streams, buy-low) use the same mechanism, so every recommendation gets a graph for free.
- **Event markers** come from injury-status changes and `news_items`: injured, return date moved, role change, traded.
- **Storage:** `evaluation_points` (subject type and ID, as_of, realized, projected_remaining, expected_total, baseline_expected_total, event refs). Points are **computed from stored snapshots**, so they can be rebuilt or backfilled at any time. **The daily snapshot is the part that can't be recreated**, which is why snapshot recording is the first thing M1 ships. That's also why trades from before the tracker ships can still be graphed back to their start.
- **We never estimate return dates ourselves.** Injury timelines enter through providers' rest-of-season projections and injury status. M1 spike question 6 checks how quickly those react to news.

---

## 9. Delivery

- **PWA** on Cloudflare Pages. Tabs: Today, This Week, Watchlist, then Scorecard (M6) and Chat (later). Swipeable decision cards, one decision per card, each with its reason. Thumbs up or down on every card.
- **Web Push** with VAPID keys (pywebpush). iOS needs the app **added to the Home Screen** (iOS 16.4+), so beta onboarding includes that step.
- **App API, a gap in the spec:** Cloudflare Pages serves static files, but the PWA has to write takes, feedback and push subscriptions, and friends need auth. **Recommendation:** FastAPI on **Google Cloud Run** (free tier, scales to zero, cold start of a second or two). It's the same vendor the spec already names as the fallback for the nightly job, so one container image serves both the API and the jobs.
- **Auth:** **Supabase Auth from M3**, the first milestone with a phone app. Sign-in is by magic link and invite-only, and the API verifies the JWT. Adam is user one, and friends join by invite at the beta, so there's no temporary auth to replace later. The PWA never talks to the database directly, which keeps the API as the single enforcement point.
- **Timeline graphs:** take, call and trade detail pages chart their `evaluation_points` (§8.1) with Recharts. The horizon is shaded, events are marked, and the final grade sits at the window's end.
- **Ops alerts:** run failed, stale source, ESPN cookies expired, late report (at 7:45 if it hasn't shipped). These go to Adam through **ntfy.sh** on a private, randomly named topic, independent of product push, so a broken PWA can't hide its own outage. Alerts never contain secrets or league data.

---

## 10. Pipeline and scheduling

```
daily (≈04:30 ET):  ingest all leagues + snapshots (raw saved) → normalize → PREFLIGHT ✋
                    → grade (close windows) → evaluate (append trajectory points for open takes/calls/trades)
                    → engine → FactSheets → brain (+ shadow setups) → guardrails → registry (frozen)
                    → ReportDocument → publish → push → run log record
                    → backup irreplaceable tables to R2 → database size check (alert at 60% / 80%)
weekly (Mon AM):    same spine + weekly overview + report card per league
season end (Apr):   export the season to Parquet in R2 → verify counts → remove its bulky rows (§5.5)
```

Until M4 adds the brain, the daily run is **engine-only**: code writes every card, including Sleeper lock-or-pass cards (M2). M4 adds the model-written prose around the same cards.

- **Preflight checks (fail closed):** every rostered player has a projection; today's games are loaded; scoring settings match the league; the identity crosswalk covers 100% of rostered players; sources are fresher than the threshold.
- **Idempotent** per (job, league, date), and stages can be resumed.
- **Early schedule.** West Coast games end around 1:30 a.m. ET, and GitHub Actions cron can run late. Starting at about 4:30 ET leaves headroom before 8 a.m. The late-report alert covers the rest. Cloud Run Jobs is the fallback (spec, Hosting step 3).

---

## 11. Hosting path

The path follows the spec, with one change of timing.

| Stage | Pipeline | Database | Raw store | PWA | API |
|---|---|---|---|---|---|
| **M0–M2** | Laptop: launchd runs `fh snapshot` twice a day; `fh ingest` by hand (ADR 0017) | **Supabase** Postgres (dev project) | Local disk | — | — |
| **M3 → beta** | GitHub Actions cron | **Supabase** Postgres (prod project, `us-east-1`) + Supabase Auth | Cloudflare R2 (raw, archive, backups) | Cloudflare Pages | Cloud Run (`us-east4`) |
| **If Actions is late** | Cloud Run Jobs + Cloud Scheduler | (same) | (same) | (same) | (same) |
| **Paid launch** | Revisit everything | | | | |

**No SQLite stage (a change from the spec).** The spec starts on SQLite and moves to hosted infrastructure before the beta. Two things favor starting on Postgres instead. Leagues and encrypted credentials live in the database from M1 (§12.1), and GitHub Actions runners don't keep files between runs, so scheduled jobs need a hosted database anyway. Starting on Supabase removes a migration from the busiest stretch of the build. **Supabase over Neon**, because it bundles Auth. The free tier allows two projects (dev and prod), and its 7-day idle pause can't trigger while a job runs every night.

**Regions and connections (ADR 0020):** the database and the API share a metro (Supabase `us-east-1`, Cloud Run `us-east4`, both Northern Virginia), which turns about 34 ms per query from a laptop into a few milliseconds. The API keeps one connection pool per process. **Offseason gotchas:** Supabase Free pauses after a week idle, and GitHub disables scheduled workflows in a public repo after 60 days without a commit. Either would stop snapshots, so M3's ops alerts watch for a missing run. Account setup: [runbooks/m3-account-setup.md](runbooks/m3-account-setup.md).

**Repository visibility (decided: public).** The fixture recorder **must redact** cookies and headers and pseudonymize leaguemates' names and team names before anything is committed. gitleaks runs before every commit and in CI.

---

## 12. Security and privacy

### 12.1 Where configuration lives

Nothing sensitive is ever in the code or in git. There are three kinds of configuration, and each has one home:

| Kind | Examples | Where it lives | Why |
|---|---|---|---|
| **System secrets** (the app's own keys) | Anthropic and LangSmith keys, database URL, the encryption key, VAPID push key | A secret manager: GitHub Secrets for jobs, Google Secret Manager for the API, and a gitignored `.env` on the laptop | These can't live in the database: the database password can't be stored inside the database, and the key that encrypts credentials can't sit next to them |
| **User and league data** | Users, friends, leagues, league IDs, watchlists, takes | The database, scoped by `user_id`, behind sign-in | It differs per user and is added through the app (`fh league add` from M1, the onboarding screen from M7) |
| **Platform credentials** | ESPN `espn_s2` and `SWID` cookies, later Yahoo OAuth tokens | The database, **encrypted** with the system encryption key (AES-GCM) | A database leak alone exposes nothing usable |

**For the paid launch:** move the encryption key to a managed key service (Cloud KMS or Supabase Vault) with rotation, and add a self-serve "reconnect ESPN" flow for expired cookies.

### 12.2 Practices

- `.env.example` lists the system secrets by name, with no values. **gitleaks** runs in pre-commit and CI.
- ESPN cookies are never logged, never written to fixtures and never sent to an LLM.
- Agents get read-only tools and the app never writes to a league platform. News text is passed to models as delimited data, never as instructions.
- Tenancy is enforced in the API's repository layer: every query is scoped by `user_id`.
- A **Console spend limit** is set on the Anthropic key from day one (spec), and the run log records the cost of each run.

---

## 13. Engineering standards

- **Python 3.12** with uv, **Ruff** (lint and format), **mypy** (strict on `domain`, `engine`, `facts`), **pytest** with recorded fixtures, **hypothesis** property tests for the engine math (for example, scoring is linear in the weights and the value of waiting never increases as games run out), and **import-linter** layer contracts.
- Test tiers: `unit`, `contract` (each adapter against recorded responses), `golden` (hand-calculated scoring), `integration` (a full pipeline run on fixtures and Postgres).
- CI: `ci.yml` (lint, types, tests, contracts, secret scan), `web.yml` (typecheck, build), `evals.yml` (regression replay). Cron workflows `daily.yml` and `weekly.yml` arrive with M3, and `grade.yml` with M6.
- **Decision log:** `docs/decisions/NNNN-title.md` (what, why, what was rejected), one per architecture choice. The decisions in this document become ADRs 0001–00NN in M0.
- **Working rules** from the spec are written into `CLAUDE.md`, so AI-assisted sessions follow them too.

---

## 14. Risk register

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| ESPN's unofficial API changes without notice | Medium / High | Adapter isolation, recorded fixtures, drift alerts from mapper validation, Sleeper as the second path |
| **Per-stat projections not exposed** (blocks the scoring layer) | Medium / **Critical** | Answered first in the M1 spike. Fallback order: Sleeper per-stat, then a cross-check source, then the spec's paid option (Basketball Monster) |
| No game-log source for lock-in variance or watchlist actuals | Medium / High | M1 spike tests espn-api box scores and player stats. Fallback: a free NBA stats API (spec lists BallDontLie and API-NBA). |
| Original M3 was overloaded (LLM report, registry, PWA, push, API and hosting move in 7 days) | Resolved in v0.2 | Split in two. The phone app and hosting (M3) are built alongside the engine (M2) and ship the weekly overview first. The LLM daily report and registry follow as M4. The database starts hosted on day one. |
| Point-in-time history can't be recovered once a day passes | Certain / High | Daily projection and injury snapshots are the **first thing M1 ships**, so every graph and grade has the history it needs |
| Actions cron runs late or is skipped | Medium / Medium | Early schedule, late-report alert, Cloud Run Jobs fallback |
| LangChain structured output uses forced tool calls (400 on current models) | Medium / Medium | M0 check, with native structured outputs pinned |
| Small samples leave cells "Incomplete" for weeks | Certain / Low | Expected and shown honestly in the report card |
| Leaguemates' data in a public repo | Medium / Medium | Redacting recorder, a review checklist, gitleaks |
| iOS push needs a Home Screen install | Certain / Low | An onboarding step in the beta guide |
| The free-tier database fills up (500 MB) | Medium / High | Typed projection columns (M2), a daily size alert at 60% and 80%, current season only in Postgres with past seasons archived to R2. Next levers: store only changed rows; Supabase Pro ($25 a month) as the last resort (§5.5) |
| Database loss on a plan with no backups | Low / **Critical** for the registry, takes and grades | Nightly backup of the irreplaceable tables to R2 (M3). Everything else rebuilds from raw. |
| Agent and chat latency | Medium / Medium | Read models, few batched tools, in-process MCP, one region (§5.4) |

---

## 15. Decisions

| # | Decision | Outcome |
|---|---|---|
| D1 | Backend for the PWA | **Decided:** FastAPI on Cloud Run |
| D2 | Hosted database and auth | **Decided:** Supabase Postgres + Auth. Moved earlier: Postgres from M0, Auth from M3. |
| D3 | Batch API in v1 | **Default: defer** (the gateway is ready for it). Revisit in M8 with cost data. |
| D4 | PWA framework | **Decided:** Vite + React + TS + vite-plugin-pwa |
| D5 | Ops alert channel | **Recommended:** ntfy.sh (§9). Waiting on Adam's confirmation. |
| D6 | Repo visibility | **Decided:** public, with the redacting recorder |
| D7 | Take format | **Decided:** direction + horizon + conviction + one-line text |
| D8 | Second model family for the Skeptic | Deferred to M8 (spec) |
| D9 | Where user and league data live | **Decided:** in the database behind auth; credentials encrypted (§12.1) |
| D10 | Timeline graphs for takes, calls and trades | **Decided:** in scope. Takes and calls ship before the beta (M6); trades ship with the trade tracker (P9). |

Still open from the spec: what per-stat projections ESPN and Sleeper expose (M1 spike), and which friends and leagues make up the beta group.
