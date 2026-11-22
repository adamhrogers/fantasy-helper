# Fantasy Helper — Phased Roadmap

**Status:** v0.6, 2026-11-15, with additions on 2026-11-16 (dates proposed, waiting on Adam) · companion to [architecture.md](architecture.md)

**Friends beta: about Dec 8, with M4's brain work and the chat running as parallel lanes (planned Nov 15, then Nov 22, then Nov 29). Without parallel lanes the plan no longer fits before the hard stop. Hard stop: Dec 15.** Each milestone ends with something Adam uses the next morning and an automated check that proves it works. Lanes (A, B, …) inside a milestone have no dependencies on each other, so they can run as parallel sub-agents or sessions once you approve.

**What changed in v0.6 (Nov 15):**
- **M3 is about ten days behind v0.5.** It was planned for Nov 7–12 beside M2. Part 1 shipped on Nov 7, then a week went to accounts and to finding that GitHub's scheduler ran every job hours late, which moved all timed jobs to Cloud Run (ADR 0025, Nov 14). Part 2 merged on Nov 15. The API, the phone app, push and backups remain, and are planned step by step in [m3-plan.md](m3-plan.md). M3 is now expected to end about **Nov 23**.
- **Everything after M3 moves with it.** Done strictly in sequence, M3, then M4, then M5 and M6, would put the beta near **Dec 12**; adding chat (below) in sequence would pass the Dec 15 hard stop. So the plan **depends on parallel lanes**: M4's lane A (the brain: `brain/`, `mcp_server/`) beside M3's phone app, which shares no files with it, and **M4b, the chat, as a third lane** once lane A's tool server exists. That puts the beta at about **Dec 8**. If a lane slips, use the cuts at the end of this page; the chat itself is not on the cut list.
- **These dates assume work on most days**, as on Nov 14 and 15. At weekends only, M3 alone runs into December and the hard stop needs the cuts at the end of this page.
- **M3's shape changed:** timed jobs are Cloud Run jobs in a morning chain, not `daily.yml` and `weekly.yml` on GitHub (ADRs 0025, 0028); sign-in is a 6-digit email code, not a magic link (decided Nov 6), and for Adam alone it's a password, with the code built behind a switch for the beta (decided 2026-11-16); the app's host is Cloudflare Workers static assets instead of Pages (decided 2026-11-16), because Cloudflare now says to start new projects there.
- **Chat moved forward** (Adam, 2026-11-15: "the earlier that we can get the chat in, the better"): it's now M4b, right after the pieces it needs exist (see "The buddy"), instead of a later idea.
- **M4's streak** ("by 8 a.m. five days in a row") moves from Nov 15–19 to the first five mornings after M4 ships.
- **Added Nov 16:** part 2's rollout finished (the morning chain ran on its own), and a smarter weekly planner with a league-savvy buddy was recorded as a direction (below), placement proposed.

**What changed in v0.5 (Nov 6):**
- **Sleeper lock-in moves from M5 to M2,** so Adam uses it in his live league from about Nov 11 instead of about Nov 23. It needs no AI, only M2's scoring and distributions. It arrives as lock-or-pass cards in an **engine-only daily report**, not a separate command. The registry's core (append-only table, tip-off guard) moves with it, because every call is logged. Lock-in v2 stays in M5.
- **M2 also lays the low-latency read path and the storage budget** (ADRs 0020 and 0021): typed projection columns, read-path indexes, the read-model tables and a database size check.
- **M3 gains** fixed regions, nightly backups to R2 and a size alert. Adam's account setup is in [runbooks/m3-account-setup.md](runbooks/m3-account-setup.md).
- **P11 gains the season archive.**
- The beta date doesn't move: M2 grows by about 1.5 days, and M5 gives them back.

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
Nov 6  ─ M2 Scoring, weekly overview, lock-in ───────── done Nov 6
Nov 7  ─ M3 Phone app + delivery                       ┐ critical path
       │   parts 1–2 (cloud snapshots, cloud reports)   │ merged Nov 7–15
       │   rollout, API, phone app, push, backups       │ Nov 15 → about Nov 23 (m3-plan.md)
Nov 18 ║ M4 lane A, the brain (beside M3's phone app)   │
Nov 24 ─ M4 Daily report + registry (streak: its first five mornings, about Nov 26–30)
Nov 25 ║ M4b Chat v0, the buddy (third lane; needs lane A's tool server and the live app)
Nov 26 ─ M5 Watchlist · signals · lock-in v2  (during the streak; its take storage is shared with the chat)
Nov 29 ─ M6 Grading + timeline graphs                  │
Dec 8  ─ ◆ FRIENDS BETA (M7) ──────────────────────────┘  (hard stop Dec 15)
Dec 9  ─ M8 Multi-agent orchestration ─────────────── about five weeks
Dec    ─ P9 Trade tracker + trade graphs · categories leagues
Jan    ─ P10 Iterate on evidence ──────────────────── Mar 15
Mar 15 ─ ◆ Season ends ─ P11 Case study
Apr–Sep 2027 ─ P12 Offseason: Yahoo, sign-up, pricing ─ fall 2027 launch
```

**Without the parallel lanes** the same work in sequence runs past Dec 15, so one of the lanes has to hold or something on the cut list goes. M4 alone, in sequence, would be Nov 24–30, M5 Dec 1–3, M6 Dec 4–9.

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

## M2 — Scoring, weekly overview and Sleeper lock-in · Nov 6 (done; planned Nov 7–12)

**Goal:** the first useful outputs, built entirely by code with no AI. That means the weekly overview, plus an **engine-only daily report** whose first cards are Sleeper **lock-or-pass** calls (moved from M5 in v0.5). Adam uses lock-in in his live league from about Nov 11.

**Status (2026-11-06): done.** PR #3, reviewed (15 findings, all fixed) and merged.
- **Built:**
  - box scores (one truth for every league, ADR 0022) and scoring, verified against both platforms
  - lock-in v1 with the registry
  - schedule (startable games), distributions (projection blended with game logs), valuation, win probability, streaming and baselines
  - FactSheet v1 with the read-model tables (migration 0005)
  - the weekly overview (`fh run weekly`) and the daily report (`fh run daily`), as local pages
- **Also built:**
  - the daily reconciliation check (ESPN season totals, Sleeper completed weeks)
  - mid-week win probability for daily-lineup leagues: points banked from our box scores and ESPN's lineup for each finished day, checked against ESPN's daily totals (exact on 2026-11-06)
- **Left:**
  - a mid-week win probability for lock-in leagues (needs lock data, parked)
  - ESPN matchup periods mapped to scoring days, before the All-Star break (until then a period that started in an earlier week is flagged, with no win probability)
  - categories-league valuation (P9)
  - a day-by-day weekly plan: the streaming planner is a whole-week batch (see "Direction: a smarter weekly planner")

These steps depend on each other, so they're ordered. B and C run in parallel at the end.

| Step | Work |
|---|---|
| 0 — Data and storage | **Game logs:** ESPN per-game actuals, with the fixture recorded first. **Migration 0003** adds `player_game_stats` (typed stat columns, a season, indexed on `(player_id, game_date)`), converts `projection_rows` to typed columns, adds composite indexes for the read paths, and adds the read-model tables `fact_sheets` and `league_player_values` (ADRs 0020, 0021). Backfill box scores from opening night (2026-10-20). Database size goes into `fh doctor` and the ingest summary. |
| A — Engine core | `scoring` (one shared box score, each league's own weights and bonuses from its settings, ADR 0022; **cross-checked against the points ESPN and Sleeper report**), `distributions` v1, `schedule` (startable games; ESPN matchup periods mapped to days), `valuation` (replacement level), `baselines` |
| D — Lock-in | `engine/lock_in.py`, by backward induction under Sleeper's rules: one game per player a week, lock after a game and before his next tip, no undo, and his final game counts if you never lock, even one he doesn't play. **Lock-or-pass cards** in the engine-only daily `ReportDocument` (`fh run daily`), showing last night's score, the value of waiting and the inputs. **The registry's core**, moved from M4: an append-only table and the tip-off guard. Sleeper's weekly points don't reliably show locks mid-week (docs/spikes/m2-scoring-check.md), and tested against Adam's real locks they fit no rule, so every open decision gets a card and locks will come from a user-set flag. `fh run daily --dry-run` runs everything and saves nothing. |
| B — Engine planning | `streaming` (greedy plan within acquisition limits), `win_probability` (normal approximation) |
| C — Facts and reports | FactSheet v1 (held to a token budget by a test), `league_player_values`, the weekly `ReportDocument`. Both reports render as local pages until M3 delivers them to the phone. |

**If M2 runs past Nov 12,** B moves to the first day of M4, so the Nov 15 streak isn't touched.

**Done when** (spec): fantasy point values for five players match a hand calculation in each points league. **Plus:**
- The golden cases live permanently in `tests/golden/`.
- The scoring and lock-in property tests are green.
- Our scores of real box scores match what Sleeper and ESPN report.
- Every Sleeper starter who played last night and has a game left gets a lock-or-pass card, logged in the registry before his next tip.

---

## M3 — Phone app and delivery · Nov 7 to about Nov 23 (planned Nov 7–12)

**Goal:** prove the whole path from scheduled job to sign-in to push before any LLM work starts.

**Status (Nov 16):** in five parts (session-handoff §7). Parts 1 and 2 are merged and part 2's rollout is done: the morning chain ran on its own on Nov 16. Part 3's code is written on a side branch; parts 3 to 5 are planned in [m3-plan.md](m3-plan.md). Because the deploy runs only from `main`, M3 merges part by part.
- **Part 1, cloud snapshots: merged and live** (PR #4, ADR 0023). The raw store is on R2, and the snapshot job runs on GitHub Actions on Eastern time. The laptop keeps saving to its own disk as an independent backup until the part 5 watchdog is live ([runbooks/cloud-snapshots.md](runbooks/cloud-snapshots.md)).
- **Dependabot: merged** (PR #5, ADR 0024). Dependency updates merge themselves once CI passes.
- **Adam's accounts:** every one M3 needs exists (GitHub `prod`, Supabase prod, the prod key and topic, R2 on 2026-11-07; Google Cloud and Healthchecks on 2026-11-14). Inventory: [runbooks/accounts-and-secrets.md](runbooks/accounts-and-secrets.md).
- **Found 2026-11-14:** GitHub's scheduler started every cloud snapshot hours late for a week (none dropped). Timed jobs need another trigger before part 2's scheduled runs are useful (session-handoff §7).
- **Decided 2026-11-14 (ADR 0025):** timed jobs move to Cloud Run Jobs started by Cloud Scheduler, watched by Healthchecks ([runbooks/scheduled-jobs.md](runbooks/scheduled-jobs.md)).
- **Snapshots on Cloud Run: live** since 2026-11-14 (PRs #12, #14), tested end to end, Healthchecks schedules set. The laptop retires after four on-time runs in a row, counting from 2026-11-15 06:17.
- **Part 2: merged 2026-11-15** (PR #16). Reports saved in a `reports` table; `migrate`, `daily` and `weekly` as Cloud Run jobs, with the morning a chain (snapshot, then daily, then weekly, each starting only when the one before is complete, ADR 0028); a run lock; the prod database migrated by the deploy; `scripts/fh-prod.sh`; Healthchecks schedules set by script. ADRs 0026, 0027 and 0028, [runbooks/prod-database.md](runbooks/prod-database.md). The deploy migrated prod to 0006 and wired the chain. **Rollout:** leagues, history and the first chained morning are still to do.
- **The snapshot overlap ended 2026-11-16** (the Mac's schedule and GitHub's are removed; Cloud Run is the only timer). **Next:** part 3 (the API and sign-in), part 4 (the phone app) and part 5 (push, backups, the exit test). All of it, in order: [m3-plan.md](m3-plan.md).

The lanes as built and planned (the first version of this table had a magic link, Cloudflare Pages and GitHub cron workflows):

| Lane | Work |
|---|---|
| A — API + auth | FastAPI on Cloud Run in **`us-east4`** with one pooled database engine per process, **Supabase Auth** (a password for Adam, who is user one, and a 6-digit email code for the beta; the API checks each token against Supabase's public keys), reports and push-subscription routes, Supabase prod project in **`us-east-1`** (ADR 0020) |
| B — PWA | **The app is built (2026-11-16; PR open); push is still to do.** Vite + React shell, sign-in, Today and This Week tabs rendering `ReportDocument` cards (the daily lock-or-pass cards and the weekly overview), Web Push (VAPID), hosted on Cloudflare Workers (static assets; Pages was ADR 0009's first plan), iOS Home Screen install |
| C — Scheduling + ops | **Built:** timed jobs on Cloud Run, started by Cloud Scheduler and chained (ADRs 0025, 0028); the raw store on R2; **run events via ntfy**; the missed-run and late-report watchdog (Healthchecks); the database size warning. **Left:** a **nightly backup** of the tables that can't be rebuilt (ADR 0021), a lock on the raw history, the exit test |

**Done when:**
- A scheduled run, with the laptop closed, pushes the daily report and the weekly overview to Adam's phone, and they open after sign-in.
- A deliberately broken run produces an ops alert.
- A backup restores into a scratch database.

**Needed from Adam:** the accounts and secrets in [runbooks/m3-account-setup.md](runbooks/m3-account-setup.md), done while M2 runs.

---

## M4 — Daily report and registry · about Nov 24–30 (lane A can start Nov 18, beside M3)

**Goal:** a daily report on Adam's phone every morning, with every call logged.

**Before the first model call:** the cloud jobs need the Claude key and the LangSmith key in Secret Manager (they have neither today), and a spend limit on the Claude key in the Console.

| Lane | Work |
|---|---|
| A — Brain baseline | LLM gateway, `baseline` graph (one Claude model), structured `ReportDraft`, guardrails (schema, numeric grounding, candidate set), graceful degradation to the engine-only report, prompt manifest, MCP server v1 (read-only, few batched tools, in-process; ADR 0020) |
| B — Registry + daily pipeline | The registry from M2 extended to every call type, with follow detection. The engine-only daily pipeline from M2 gains the brain, preflight checks and news adapter v1. |

**Done when** (spec): the report reaches Adam's phone by 8 a.m. five days in a row (its first five mornings, about Nov 26–30), and every call in it is in the registry. **Plus:** every LLM call has a LangSmith trace ID on its registry row, and the run log shows cost per report.

---

## M5 — Watchlist, belief signals, lock-in v2 · about Nov 26–28 (during M4's streak), or Dec 1–3

| Lane | Work |
|---|---|
| A — Takes | API and PWA form: player search, direction, horizon, conviction, one-line take, in under 10 seconds on a phone |
| B — Signals | `engine/signals.py`, signal cards in the daily report, registry call types `pick_up_signal` and `buy_low` |
| C — Lock-in v2 | Version 1 shipped in M2. Version 2 adds: decisions across the whole roster (bench a player after a pass so a bench player's game counts), matchup-aware risk (chase variance when behind), an **evening alert** from the 17:17 snapshot when a player is ruled out before his last game, and the one-tap "I locked / passed" button in the PWA: Sleeper doesn't reliably show locks mid-week, so this is how the app learns them |

**Done when** (spec): signals and lock calls appear in the report and in the registry. **Plus:** the lock-in v2 property tests are green.

---

## The buddy: a chat you can talk to all the time (stated 2026-11-15, placement proposed)

**Adam's words, 2026-11-15:** a chatbot "like a buddy that you can talk to all the time" is "very important", and "the weekly and daily report is just the deliverable right now, but that is the main thing that I want in the future." He talks to it about players and moves, gives his opinions, and it remembers them. The design in his head: **the data we collect plus his opinions are what the agent works from, and the grading is the loop that keeps it honest.**

**Where this stands in the docs before today:** the pieces for it were designed in (ADR 0003 and 0020: one read-only tool server and a fast read path "for the future chat"; a Chat tab marked "later" in the app layout), but chat itself was listed under "not in version 1", to ship only if the report card supported it. That is superseded: it is now a goal. What is **not** yet designed is how the chat remembers what he says.

**Placement, as early as it logically can go (Adam, 2026-11-15: "the earlier that we can get the chat in, the better... that's the interactive portion"; the dates are a proposal):** chat v0 is **M4b**, a third lane that starts the day its three prerequisites exist, about **Nov 25**, and runs about five days. It reuses everything M3 to M5 build and adds little of its own.

| It needs | From | Ready about |
|---|---|---|
| The API and the live phone app (a Chat tab, a chat route that streams its reply) | M3 parts 3 and 4 | Nov 23 |
| The AI gateway and the read-only tool server (the chat reads facts through the same tools the reports' brain uses) | M4 lane A | Nov 24 |
| A place to save his opinions as takes, and the confirm step | M5's take storage, built first and shared | Nov 25 |
| The Claude and LangSmith keys in the cloud, and a monthly spend cap | A short set-up step before M4 | Nov 18 |

Chat v0 does **not** wait for M5's signals, the daily report's AI writing, grading or the beta. Those proceed in their own lanes.

**Memory = his opinions as takes.** When he says "I think Player X is going to break out", the chat proposes a take (player, direction, how long, how sure) and he confirms it. Code then saves it. This keeps two existing rules: tools stay read-only, so the AI never writes to the record by itself (ADR 0003), and saved opinions are never edited, only superseded, so they can be graded later (ADR 0013). Casual remarks that aren't confirmed takes go into a plain notes memory that the chat may read and that is never graded.

**The loop = the grading.** His takes and the app's calls are graded the same way (M6). The chat can then say honestly "your last four takes on rookies were right, your calls on bigs were wrong", and the brain's prompts can be tuned against graded history.

**Chat v0 is for Adam only:** text in the app; reads the day's facts, his takes and the call history; can propose takes; can explain any card in a report. **Later:** proactive messages ("look at this"), voice, chats for friends, and the full panel of specialist AIs (M8).

**Two things designed in from the start:** the buddy must not just agree with him (a built-in skeptic voice, the Skeptic and Your Voice agents of M8 pulled forward in simple form); and every message costs money and time, so it has a monthly spend cap, and the always-warm service ADR 0020 deferred "until chat ships" is decided during this milestone.

**Done when:** from the phone, Adam can ask about a player or a move and get an answer built only from facts the system holds, say an opinion, confirm it, and see it appear as a take that the next day's chat remembers.

## Direction: reports shaped by each user (stated 2026-11-15, not scheduled)

Adam wants the daily and weekly reports to be **customizable per person**: a friend's report can look different from his, and each user can say what they do and don't want to see. The interface should be **editable and dynamic**, not one fixed page. **This is a direction, not part of M3 to M7.**

What keeps the door open, so it isn't rebuilt later:
- **Reports are already lists of cards, not pages.** A card has a kind (a move, a lock-or-pass, a recap, a matchup) and its data; the app draws whatever cards arrive. The phone app (M3 part 4) must keep drawing from that list generically, and a card kind it doesn't recognize must still show as a plain card.
- **Customizing means choosing among blocks the code computes**, never an AI making up numbers: which kinds of cards, in what order, with what thresholds. A genuinely new block is new code. This keeps the rule that code computes every number.
- **The chat is the natural way to ask for it** ("show me this, drop that"), saved as a per-user preference after he confirms it, the same way opinions become takes.
- **Where it would land:** after the beta shows what friends actually want to change. It needs per-user settings stored in the database (a migration), the reports jobs reading them (so each user's report is built to their preferences), and an editing screen or the chat. Not before M7.

## Direction: a smarter weekly planner, and a buddy that knows how leagues work (stated 2026-11-16, placement proposed)

**Adam's point, 2026-11-16:** the weekly plan should reason about a week the way a careful manager does, and the agent (the buddy) should understand how these leagues work: how slots, injured reserve, add limits and timing interact, and what a manager could do about each. Today's weekly plan is a first version, and these are its known limits (read from `engine/schedule.py`, `engine/streaming.py` and `facts/weekly.py`):

- **Who counts is a snapshot of now.** Only players who are not in an injured-reserve slot count, and anyone whose latest status says out is treated as out for every remaining day that has no provider projection of its own, when often a status is about one game, a rest day or a recent event. A player on injured reserve is never planned back in, though his return needs a roster spot, which means a planned drop.
- **Every swap is scored as if made at the start of the week.** The dropped player is gone for every remaining day, and the add limit is spent all at once as a list of changes, with nothing said about which day to make each one, and nothing kept back for news. A swap made after the dropped player's game would keep that game and add the new player's games; the planner can't express that.
- **The count of who can start each day isn't checked** against the league's real lineup, the way banked points are checked against the platform's daily totals. A day's count was found one lower than the league's real lineup on 2026-11-16; the cause was the slot filling, fixed the same day (session-handoff §7). **Since 2026-11-16 the report says who it counted and who it left out, and why** (item 1 below), so the next mismatch can be traced from the report itself.

**What it should become:**
1. **Say what it counted.** For each day, who is in the lineup, who was left out, and why; checked against the league's real lineup. This is the smallest piece and a trust issue: a wrong count has to be visible. **Built 2026-11-16 (PR 21):** each day of the weekly sheet lists the players who had a game but weren't counted, with one of three reasons (not expected to play; no provider projects the game; in a starting slot but outranked), and the sheet lists players the schedule has no game for at all that week. The weekly report shows both in one card, only when there is something to say. A player who isn't expected to play no longer takes a slot or counts as a startable game, which fixes the overcount. **Still open in this item:** checking the day's count against the platform's own lineup (the count is now explained, not yet reconciled).
2. **Availability over time, not yes or no.** Out for tonight, out for the week, expected back on a day with a probability; injured-reserve returns as planned roster events.
3. **A day-by-day plan.** For each remaining day: the lineup, the moves to make that day, and the adds left. The add limit is a budget spent across the week, with some held back, and the planner picks each move's day (a swap after the dropped player's last useful game, an add before his best stretch).
4. **The buddy knows the mechanics.** The league's rules (slots, injured reserve, add limits, lock rules, how a week is scored) and the planner's reasoning for each suggestion come to the chat as facts it reads through the same read-only tools as the reports' brain, so it can explain a plan and answer "what if I do it a day later" from code that computed it. This keeps the rule that code computes every number and the AI explains.

**Where it lands (agreed by Adam 2026-11-16; m3-plan.md decision 7):** items 1 to 3 are pure engine and facts work (`engine/`, `facts/`, `reports/weekly.py`), with no files shared with the app, the API or the brain, so they can run as a lane beside M3's Phases 3 to 5. Item 1 first, because it is small and the cause of the count mismatch needs finding anyway. Items 2 and 3 are bigger and should land before the chat (M4b), which will be asked about these plans, and before the beta, because friends will read weekly plans. Item 4 belongs to M4b.

**Done when (proposed):** for a real week in each points league, the plan lists the lineup and the moves day by day, its daily counts match the league's own, and Adam either agrees with it or can see exactly why it differs from what he would do.

## Direction: a daily report that says what happened in each league (stated 2026-11-16, placement proposed)

**Adam's point:** the morning report should tell him what happened, in every league, not only give lock calls for the one lock-in league. Today the daily report holds only lock-or-pass cards (plus a recap) for lock-in leagues, so a league on another format gets no daily card at all.

**What he wants, for every league, in that league's own scoring (rule 9):**
- **Last night across the league:** the standout performances, the free-agent and waiver pickups that did well, and what was expected against what wasn't.
- **His own team:** who did well, who did badly, measured against the projection.
- **The matchup:** the points he scored that day, the points his opponent scored, and the matchup total so far.
- **Lock-in leagues, on top of that:** the lock-or-pass calls stay the core. They also need to weigh what the **opponent** has locked, which the app can't see (the matchup response carries weekly points per player and no lock flags, session-handoff §5). The engine should treat the unseen part as uncertainty and not guess, and where the matchup stands should shape how much risk a call takes (this is lock-in v2's matchup-aware risk, M5 lane C). Whether the opponent's displayed points count only locked games is checkable with real data once a week has games; check before relying on it.

**Telling the app a player is locked.** Locks can't be read from the platform, so the app learns them from the user: the one-tap "I locked" and "I passed" button on a lock card, planned in M5 lane C. Until it exists, a card can recommend a player Adam already locked. It needs a small table, an API route and the button, so it follows the API and the app (M3 parts 3 and 4).

**Principles, unchanged:** code computes every number (points, ranks, expected against actual); the brain (M4) only writes the narrative around them. An engine-only version of these cards, plain facts with no model, is useful on its own and can come first. Each kind of card is a block the customizable-reports direction can switch on and off.

**Where it lands (proposed, waiting on Adam; m3-plan.md decisions 8 and 9):** the engine-only "what happened" cards are engine, facts and reports work with no files shared with the app or the API, so they can run as a lane beside M3's Phases 3 to 5 and be ready for the phone's Today tab; the lock button moves up from M5 to right after the app's first deploy.

**Done when (proposed):** for a real morning in each league, the report says what happened last night in that league's scoring, his day's points and his opponent's match the platform's own daily totals, and in a lock-in league a card never recommends a player Adam has already locked.

**Built (2026-11-16, Adam approved the lane the same day; [ADR 0033](decisions/0033-the-daily-report-says-what-happened-in-every-league.md)):** the engine-only "what happened" cards, for every league kind, with no model and no migration. Each league's daily report now holds, after any lock-or-pass cards:
- **the matchup:** last night's points for both teams, the matchup so far, and where it stands, counted from box scores and the lineup each team had that day (ESPN points leagues), or category by category for the week (ESPN categories leagues);
- **his team:** who beat or missed the projection, who didn't play, and a bench player with a good game;
- **the league's standouts**, **recent pickups that did well**, and **what was expected against what wasn't** (the projection pulled before the games, blended with each player's own games, so nothing reads the future).

Every number is valued by the league's own rules (a categories league, which has no points, gets an index in standard deviations). **Checked against the platform:** ESPN's own daily points for points leagues and its running category totals for categories leagues; the run reports a disagreement as a warning. A lock-in league gets the same cards, with the matchup card showing last night's game scores before locks next to the platform's own week score: it gives no total of ours and guesses no locks. **Not built, and still open:** the opponent-aware part of lock-in v2, the "I locked / I passed" button (needs the API and the app), and the raw per-stat running totals ESPN sends for points leagues as a further check on box scores.

---

## M6 — Grading and timeline graphs · about Nov 29–Dec 4, or Dec 4–9

| Lane | Work |
|---|---|
| A — Grader + trajectories | `grade.yml` nightly; outcomes per call type; follow detection; **`engine/trajectory.py` + `evaluation/trajectories.py`**, which append one point per open take and call each night and can rebuild history from snapshots |
| B — Metrics + cards | Lift, Brier and calibration, MAE, bootstrap CI, grade scale and min-sample rule, weekly report card, rules-based miss causes |
| C — Scorecard + graphs | PWA Scorecard tab; **take and call detail pages with the timeline graph** (horizon band, injury and news markers, final-grade marker); LangSmith dataset export; `evals.yml` skeleton |

**Done when** (spec): the first report card exists for both leagues, and every closed call has a grade or shows Incomplete. **Plus:** every take has a timeline graph that updates nightly, backfilled to the day it was logged.

---

## M7 — Friends beta · from about Dec 8

| Work |
|---|
| Invite flow on the existing Supabase Auth. A **league onboarding screen** in the PWA (Sleeper league ID, or ESPN cookies stored encrypted) replaces `fh league add` for friends. Home Screen install guide. |
| Per-card thumbs up/down, the weekly question, engagement metrics (opens, active days, on-time delivery, cost per report) |
| Runbooks: onboarding a friend, ESPN cookie refresh, failed or late run. The RatBall note goes out if any RatBall-derived data reaches friends. |

**Done when** (spec): the first friend receives reports. **Plus:** a tenancy test proves one user can't read another's data or credentials.
**Needed from Adam:** the list of beta friends and their leagues (open question in the spec).

---

## M8 — Multi-agent orchestration · from the beta, about five weeks (was Nov 30–Dec 31)

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

Monthly deep dive on the five weakest cells → improvement tickets with traces attached → fix → replay. The rest of the spec's "not in version 1" list (evening report, ghost moves) ships only if the report card and feedback support it. **Chat is no longer on that list:** Adam made it a core goal on 2026-11-15 (see "The buddy" above).

## P11 — Season close and case study · Mar 15 →

Final scorecard per league, the trend across the season, the effect of each change, a short demo and the case study (from `docs/case-study/`).

**Season archive (ADR 0021):** after the finals, export the 2026-27 season's tables to Parquet in R2 (`archive/season=2027/`), verify the row counts, and remove the season's bulky rows (projections, daily rosters) from Postgres. The archive is the multi-year memory: projection accuracy by provider, player aging, our own calls, all readable with DuckDB. The same job runs every April after that.

## P12 — Offseason · Apr–Sep 2027

Yahoo adapter (OAuth, which also solves sign-up for Yahoo users), self-serve ESPN connect, a managed key service for credential encryption, pricing, a hosting review for paying users, a decision on a native app, and the fall 2027 launch.

---

## When the repo goes private

**Decided 2026-11-07: stay public for now** (ADR 0012). Nothing that's hard to copy is in the repo yet, going private is one setting, and a private repo on the free plan loses free secret scanning, the `prod` environment's branch rule and required CI checks (those need GitHub Pro). Go private at the **first** of these:
- before the M8 council prompts land: the most copyable part of the product;
- before anyone pays (P12);
- if strangers start forking or starring the repo.

Users' league data never lives in the repo, so the friends beta doesn't force it. When it happens: budget GitHub Pro ($4 a month), check the `prod` branch rule and the `main` ruleset survived (without the ruleset, Dependabot's PRs stop merging themselves and wait for a person, ADR 0024), and watch Actions minutes (2,000 a month free).

## Critical path and the levers if it slips

```
M1 snapshots + per-stat projections ─► M2 scoring ─► M3 phone path ─► M4 daily report ─► M6 grading ─► beta
                                                     M4 lane A (the brain) ──┘ (parallel)
```

**To pull the beta earlier** (v0.6 already assumes the first two, and a third lane for the chat, for the Dec 8 date): run M4's lane A beside M3's phone app (they share no files), start M5's take-entry form during M4's streak week, and accept the first cut below.

If the beta date is at risk, cut in this order (keeping the Dec 15 hard stop): (1) PWA polish, keeping plain cards; (2) lock-in v2 moves to the first beta week (v1 already shipped in M2); (3) the timeline graph ships as a plain line, with event markers added the next week; (4) the beta starts with the Sleeper league only. **Never cut:** daily snapshots, the registry, the tip-off guard, tracing or the nightly backup. Without them the season produces no evidence and the graphs have no history.
