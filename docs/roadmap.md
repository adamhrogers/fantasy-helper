# Fantasy Helper — Phased Roadmap

**Status:** v0.4, re-baselined 2026-11-06 when M1 finished · companion to [architecture.md](architecture.md)

**Friends beta: Nov 29 (planned Nov 15, then Nov 22). Hard stop: Dec 15.** Each milestone ends with something Adam uses the next morning and an automated check that proves it works. Lanes (A, B, …) inside a milestone have no dependencies on each other, so they can run as parallel sub-agents or sessions once you approve.

**What changed in v0.4 (Nov 6):** M1 finished on Nov 6, about five days after the v0.3 plan, so M2 onward moves five to seven days and the beta moves to **Nov 29**, the Sunday after Thanksgiving and still two weeks inside the hard stop. Game logs (box scores) moved from M1 to M2, where the distributions first need them.

**What changed in v0.3 (Oct 25):** M0 started on Oct 25 instead of Oct 19, so every milestone moves about six days and the beta moves to **Nov 22**, still three weeks inside the Dec 15 hard stop. To win back Nov 15, see the levers at the end.

**What changed in v0.2:**
- **The old M3 is split.** The phone app and hosting became their own milestone (M3), built **alongside** the engine (M2). It delivers the weekly overview first, so the phone path is proven before the LLM arrives. The LLM daily report and registry are now M4.
- **The database is hosted from day one.** There's no SQLite stage, and leagues and encrypted credentials go into Supabase from M1.
- **Timeline graphs** for takes and calls ship in M6, before the beta. Trade graphs ship with the trade tracker in P9, backfilled to M1.
- **Dates shifted:** M0 starts on approval (the original Oct 16–19 window has passed). The beta date holds.
- **Numbering:** from M3 on, numbers differ from the spec's (spec M3 → M3 + M4, spec M4 → M5, and so on).

## Timeline at a glance

```
Oct 25 ─ M0 Foundation ─────────────────────────────── done Nov 6 (NBA tip-off was Oct 20)
Nov 6  ─ M1 Data spike ─────────────────────────────── done Nov 6; snapshots since Nov 6
Nov 7  ─ M2 Scoring + weekly overview (+ game logs)    ┐ critical path
       ║ M3 Phone app + delivery  (in parallel with M2) │
Nov 13 ─ M4 Daily report + registry (5-day streak Nov 15–19)
Nov 20 ─ M5 Watchlist · signals · lock-in              │
Nov 23 ─ M6 Grading + timeline graphs                  │
Nov 29 ─ ◆ FRIENDS BETA (M7) ──────────────────────────┘  (hard stop Dec 15)
Nov 30 ─ M8 Multi-agent orchestration ─────────────── Dec 31
Dec    ─ P9 Trade tracker + trade graphs · categories leagues
Jan 1  ─ P10 Iterate on evidence ──────────────────── Mar 15
Mar 15 ─ ◆ Season ends ─ P11 Case study
Apr–Sep 2027 ─ P12 Offseason: Yahoo, sign-up, pricing ─ fall 2027 launch
```

---

## M0 — Foundation · Oct 25–Nov 6 (done)

**Goal:** a repo where the spec's rules are enforced by tooling before any feature exists.

**Status (Nov 6):** built and passing locally: 60 tests (database tests included), mypy strict, import contracts, pre-commit hooks, `alembic check`. The structured-output check is done (ADR 0015). The Supabase dev project is migrated to 0001, and `fh doctor` has confirmed a LangSmith trace and an ntfy alert on Adam's phone. Remaining: the first CI run on GitHub. Milestone dates get re-baselined when M1 starts.

| Lane | Work |
|---|---|
| A — Python skeleton | uv project, package layout per [repo-structure.md](repo-structure.md), Ruff, mypy, pytest, import-linter contracts, pre-commit with gitleaks, `config` (system secrets only, `.env.example`) |
| B — Domain + database | Pydantic domain models v1 (including `ReportDocument`, so M3 can build against sample data), Supabase dev project, SQLAlchemy and Alembic baseline, credential encryption (`storage/crypto.py`) |
| C — Observability + CI | structlog, LangSmith wiring, a traced smoke test, `ci.yml` with a Postgres service container |
| D — Docs | ADRs from the approved decisions, `CLAUDE.md` with the working rules, README |

**Checks:** confirm the `langchain-anthropic` structured-output method uses native JSON schema rather than forced tool calls.
**Done when:** CI passes, a test run appears as a trace in LangSmith, the import contracts are green, and the first Alembic migration applies to the Supabase dev project.
**Needed from Adam:** LangSmith API key, an empty **public** GitHub repo, and a Supabase account (I'll walk you through creating the two projects).

---

## M1 — Data spike · Nov 6 (done)

**Goal:** all three leagues load with one command, and history starts accumulating.

**Status (Nov 6): done.** `fh snapshot` runs twice a day on launchd; `fh league add` stored all three leagues; `fh ingest` loads everything in about 30 seconds and is idempotent. First live run: 1,100 ESPN players, 142/142 Sleeper-rostered players linked to ESPN, 1,200 scheduled games, 9 projection snapshots (8,490 rows), 226 injury status changes, and full rosters, matchups and transactions for each league. Answers in [the spike doc](spikes/m1-data-sources.md). **Moved to M2:** game logs (box scores), which the distributions need first.

| Lane | Work |
|---|---|
| A — ESPN adapter | `espn-api` wrapper: settings and scoring weights, rosters, free agents, transactions (including trades), matchups, pro schedule, projections, injury status, box scores. Mapper to domain. Recorder with redaction. |
| B — Sleeper adapter | League, scoring settings, rosters, matchups, transactions, NBA players, state, undocumented projections endpoint |
| C — Storage, identity, leagues | Tables v1, **`fh league add`** (stores the league and its encrypted cookies in the database, never in a file), raw store, player identity crosswalk, run log, `fh ingest`. **Ships first: a daily snapshot of projections and injury status**, because a missed day can never be recovered. |

**Spike questions, written up in `docs/spikes/`:**
1. Which **per-stat** projections do ESPN and Sleeper expose (season, week, day)? *Blocks M2.*
2. Where do **per-game logs** come from for players who aren't rostered?
3. Does ESPN expose league scoring weights through the API?
4. Are Sleeper **lock actions** visible through the API?
5. Which free source can supply **injury and news** text?
6. How quickly do providers' **rest-of-season projections react to injuries**? This drives the timeline graphs.

**Done when** (spec): one command loads all leagues; tests run on recorded responses; a note records which per-stat projections each platform exposes. **Plus:** 100% of rostered players resolve in the crosswalk, and the snapshot job has run every day since it shipped.
**Needed from Adam:** ESPN league IDs and cookies, typed into the `fh league add` prompt (stored encrypted, never saved to a file); the Sleeper league ID; a Claude API key with a monthly spend limit (stored as `FH_ANTHROPIC_API_KEY`).

---

## M2 — Scoring layer and weekly overview · Nov 7–12

**Goal:** the first useful output, the weekly overview, built entirely by code with no AI.

| Lane | Work |
|---|---|
| A — Engine core | `scoring`, `valuation` (replacement level), `schedule` (startable games), `distributions` v1, `baselines` |
| B — Engine planning | `streaming` (greedy plan within acquisition limits), `win_probability` (normal approximation) |
| C — Facts + report | FactSheet v1, weekly `ReportDocument` builder |

**Done when** (spec): fantasy point values for five players match a hand calculation in each league. **Plus:** those cases live permanently in `tests/golden/`, and property tests on scoring are green.

---

## M3 — Phone app and delivery · Nov 7–12 (in parallel with M2)

**Goal:** prove the whole path from scheduled job to sign-in to push before any LLM work starts.

| Lane | Work |
|---|---|
| A — API + auth | FastAPI on Cloud Run, **Supabase Auth** (magic link; Adam is user one), reports and push-subscription routes, Supabase prod project |
| B — PWA | Vite + React shell, sign-in, Today and This Week tabs rendering `ReportDocument` cards, Web Push (VAPID), Cloudflare Pages, iOS Home Screen install |
| C — Scheduling + ops | `daily.yml` and `weekly.yml` on GitHub Actions, raw store moves to R2, **ops alerts via ntfy** (failed run, stale source, expired cookies, late report) |

**Done when:** a scheduled run, with the laptop closed, pushes the weekly overview to Adam's phone, and it opens after sign-in. A deliberately broken run produces an ops alert.

---

## M4 — Daily report and registry · Nov 13–19

**Goal:** a daily report on Adam's phone every morning, with every call logged.

| Lane | Work |
|---|---|
| A — Brain baseline | LLM gateway, `baseline` graph (one Claude model), structured `ReportDraft`, guardrails (schema, numeric grounding, candidate set), graceful degradation to the engine-only report, prompt manifest, MCP server v1 (read-only tools) |
| B — Registry + daily pipeline | Append-only registry with tip-off guard, `pipeline/daily.py`, preflight checks, news adapter v1, daily `ReportDocument` |

**Done when** (spec): the report reaches Adam's phone by 8 a.m. five days in a row (Nov 15–19), and every call in it is in the registry. **Plus:** every LLM call has a LangSmith trace ID on its registry row, and the run log shows cost per report.

---

## M5 — Watchlist, belief signals, lock-in · Nov 20–22

| Lane | Work |
|---|---|
| A — Takes | API and PWA form: player search, direction, horizon, conviction, one-line take, in under 10 seconds on a phone |
| B — Signals | `engine/signals.py`, signal cards in the daily report, registry call types `pick_up_signal` and `buy_low` |
| C — Lock-in | `engine/lock_in.py` (backward induction), lock/pass cards, plus a one-tap "I locked / passed" confirmation if spike Q4 found that locks aren't visible |

**Done when** (spec): signals and lock calls appear in the report and in the registry. **Plus:** lock-in property tests are green.

---

## M6 — Grading and timeline graphs · Nov 23–28

| Lane | Work |
|---|---|
| A — Grader + trajectories | `grade.yml` nightly; outcomes per call type; follow detection; **`engine/trajectory.py` + `evaluation/trajectories.py`**, which append one point per open take and call each night and can rebuild history from snapshots |
| B — Metrics + cards | Lift, Brier and calibration, MAE, bootstrap CI, grade scale and min-sample rule, weekly report card, rules-based miss causes |
| C — Scorecard + graphs | PWA Scorecard tab; **take and call detail pages with the timeline graph** (horizon band, injury and news markers, final-grade marker); LangSmith dataset export; `evals.yml` skeleton |

**Done when** (spec): the first report card exists for both leagues, and every closed call has a grade or shows Incomplete. **Plus:** every take has a timeline graph that updates nightly, backfilled to the day it was logged.

---

## M7 — Friends beta · from Nov 29

| Work |
|---|
| Invite flow on the existing Supabase Auth. A **league onboarding screen** in the PWA (Sleeper league ID, or ESPN cookies stored encrypted) replaces `fh league add` for friends. Home Screen install guide. |
| Per-card thumbs up/down, the weekly question, engagement metrics (opens, active days, on-time delivery, cost per report) |
| Runbooks: onboarding a friend, ESPN cookie refresh, failed or late run. The RatBall note goes out if any RatBall-derived data reaches friends. |

**Done when** (spec): the first friend receives reports. **Plus:** a tenancy test proves one user can't read another's data or credentials.
**Needed from Adam:** the list of beta friends and their leagues (open question in the spec).

---

## M8 — Multi-agent orchestration · Nov 30–Dec 31

**Goal:** prove or disprove that the council beats one model.

1. `council` graph scaffold with the same `ReportDraft` contract and shadow-run plumbing (`is_shadow`).
2. Agents are added **one at a time**, each in a shadow run for at least a week of graded calls: Scout → Analyst → Skeptic (second model family) → Your Voice → Editor.
3. `evals.yml` replays stored FactSheets on every change to the brain or prompts and fails if lift drops.
4. Decide on the Batch API using the cost data from the run log (D3).

**Done when** (spec): each agent stays only if its report card beats the single-model baseline. **Needed from Adam:** the second provider's API key (D8).

---

## P9 — Trade tracker and categories leagues · December

- **Trade tracker** (spec feature): every league trade graded short term (first two weeks) and long term (rest of season), with a **timeline graph per trade** showing each side's value over time. History is **backfilled to M1** from stored snapshots and transactions.
- **Categories leagues:** league-relative z-scores, a category win-probability model (Monte Carlo), category calls and grading. Adam's 9-category ESPN league goes live.

## P10 — Iterate on evidence · Jan 1–Mar 15

Monthly deep dive on the five weakest cells → improvement tickets with traces attached → fix → replay. The rest of the spec's "not in version 1" list (evening report, ghost moves, chat) ships only if the report card and feedback support it.

## P11 — Season close and case study · Mar 15 →

Final scorecard per league, the trend across the season, the effect of each change, a short demo and the case study (from `docs/case-study/`).

## P12 — Offseason · Apr–Sep 2027

Yahoo adapter (OAuth, which also solves sign-up for Yahoo users), self-serve ESPN connect, a managed key service for credential encryption, pricing, a hosting review for paying users, a decision on a native app, and the fall 2027 launch.

---

## Critical path and the levers if it slips

```
M1 snapshots + per-stat projections ─► M2 scoring ─► M4 daily report ─► M6 grading ─► Nov 29 beta
                                       M3 phone path ──┘ (parallel)
```

**To pull the beta earlier:** run M2 and M3 as parallel sessions (they share no files), start M5's take-entry form during M4's streak week, and accept the first cut below.

If the beta date is at risk, cut in this order (keeping the Dec 15 hard stop): (1) PWA polish, keeping plain cards; (2) Sleeper lock-in moves to the first beta week; (3) the timeline graph ships as a plain line, with event markers added the next week; (4) the beta starts with the Sleeper league only. **Never cut:** daily snapshots, the registry, the tip-off guard or tracing. Without them the season produces no evidence and the graphs have no history.
