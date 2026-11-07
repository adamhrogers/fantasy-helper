# Run events: what your phone hears, retries, and how to add an event

**Written:** 2026-11-06. **Code:** `src/fantasy_helper/observability/events.py` (events), `src/fantasy_helper/pipeline/retry.py` (retries). **Decision:** ADR 0011.

Run events are the app's **event monitoring**. Every run (snapshot, ingest, and later the daily and weekly runs) tells your phone:
- when it starts;
- as each step finishes;
- whether it's retrying;
- how it ended.

They're about **runs only**, and they read the same with 3 leagues or 15: how many loaded, how many failed and why. Anything you act on, like "your report is ready" or "lock before tonight's tip", comes from the phone app itself (Web Push, from M3), never from these events.

---

## How it works

```
 a run (pipeline code)                     RunEvents                         your phone
 ─────────────────────                     ─────────                         ──────────
 events.started()          ─────►  log it  ─►  quiet?  ─►  Notifier  ─►  ntfy.sh topic  ─►  ntfy app
 events.step("…", "counts")        (always)    progress    (ntfy, or the
 events.retrying(2, 3, …)                      off → skip  log if no topic)
 events.succeeded("counts")
 events.warned([...]) / events.failed("…", [...])
```

1. **A run creates one `RunEvents`** with its kind (`RunKind`: snapshot, ingest, daily, weekly, grade) and a label ("Morning snapshot", "Ingest", "Daily run").
2. **It reports as it goes:** `started()` once, `step(name, detail)` after each stage, `retrying(...)` before each retry, and at the end exactly one of `succeeded()`, `warned()` or `failed()`.
3. **Every event is logged first** (`run_event` in the JSON logs), so the log and the phone always agree.
4. **Quiet events are skipped** when `FH_NOTIFY_PROGRESS=false`. Retries, warnings and failures always go out.
5. **The notifier publishes to your private ntfy topic.** Without a topic (tests, a fresh checkout), events go to the log only.
6. **Sending can never break a run.** If ntfy is down, the error is logged and the run carries on.

## How loud each event is

| Event | Priority | On your phone | Can be switched off |
|---|---|---|---|
| `started`, `step`, `succeeded` | low (2) | No sound; sits in the notification list. `succeeded` ends with "Took 42 s." | Yes (`FH_NOTIFY_PROGRESS=false`) |
| `retrying` | default (3) | Normal sound: something failed in a way a retry can fix, and it's trying again | No |
| `warned` | default (3) | Normal sound: finished, but something needs a look | No |
| `failed` | high (4) | Loud: what failed, how many attempts, the last step that finished | No |
| `failed(urgent=True)` | urgent (5) | Loudest: nothing worked (every dataset, or every league, failed) | No |

The ntfy app lets you tune sound per priority. On iPhone, the ntfy app's notification settings decide how quiet "low" is.

## Retries: can we rerun this?

There are three layers, from fastest to slowest:

| Layer | What retries | How | Covers |
|---|---|---|---|
| 1. Each request | One HTTP call | 3 tries within about 6 s, on connection errors, 429 and 5xx (`adapters/_http.py`) | Blips |
| 2. Each part of a run | A dataset (snapshot) or a league (ingest) that still failed | Classified first. If a retry can help, only the failed parts run again, after **2 min**, then **5 min**: **3 attempts** in all (`pipeline/retry.py`) | Outages of a few minutes |
| 3. A run that never starts | The Mac was off, or a scheduler skipped the run | Can't retry itself: the missed-run watchdog (M3) catches it | Long gaps |

**The classifier is plain rules, not a model:** the cause of the failure decides.

| Cause (`FailureKind`) | Retried? | What the phone says |
|---|---|---|
| Couldn't connect; the source's servers erred (5xx); rate-limited (429) | Yes | "…, after 3 attempts" if it never recovered |
| An unexpected response (not JSON, suspiciously small) | Yes: it may be a partial outage | |
| An earlier request it depends on failed (Sleeper's state) | Yes, if that request is being retried | |
| The database was unreachable | Yes | |
| Access denied (expired ESPN cookies) | No: someone has to act | "…; not retried (retrying can't fix it)" |
| Not found (league or endpoint gone) | No | |
| The platform changed its data format; a bug in our code | No: the code has to change | |
| Couldn't save the file | No | |
| The raw store (R2) was unreachable | Yes: nothing was saved, so trying again can help | |

**Why 3 attempts:** request-level retries already absorb blips. Two waits (2 and 5 minutes) cover a short outage, and a 06:30 run still finishes well before the 8 a.m. report. Outages longer than about 7 minutes rarely clear by waiting a little more, and those cases are what the late-report and missed-run alerts are for.

**What gets rerun:**
- **Snapshots:** only the datasets that failed. One that succeeded is never fetched twice (Sleeper asks for its player list at most once a day).
- **Ingest:** the whole run again, because it's idempotent: what loaded stays loaded, and the failed leagues get another go. One league failing, even with a bug, never stops the others. Each league is stored in its own savepoint.

**Example: a short ESPN outage at 06:30:**
1. `Morning snapshot started` (quiet)
2. `Morning snapshot: retrying (attempt 2 of 3)` (normal): "1 of 9 datasets failed in a way a retry can fix. • espn.game_logs: the source's servers erred. Trying again in 2 min."
3. `Morning snapshot succeeded` (quiet): "9/9 datasets saved, 3.9 MB. Took 2.8 min."

**Example: expired cookies in one of 15 leagues:**
- `Ingest failed` (loud): "Leagues: 14 of 15 loaded. … • 1 ESPN league failed: access denied (expired cookies? re-run fh league add); not retried (retrying can't fix it)"

## What you get today

**Morning and evening snapshots** (GitHub Actions at 06:17 and 17:17 Eastern from M3 part 1, ADR 0023; the Mac's 06:30 and 17:30 jobs during the overlap):
- `Morning snapshot started` (quiet): "9 datasets to save."
- `Morning snapshot: retrying (attempt 2 of 3)` (normal), only when needed. An R2 outage while saving counts as retryable ("the raw store (R2) was unreachable").
- `Morning snapshot succeeded` (quiet): "9/9 datasets saved, 3.9 MB. Took 41 s." While the Mac and the cloud overlap, the second morning run adds "1 already saved today by another run": Sleeper's player list is fetched once a day.
- `Morning snapshot incomplete` (loud): which datasets failed, why, and after how many attempts. The others are still saved.
- `Morning snapshot failed` (urgent): every dataset failed.
- `Morning snapshot crashed` (urgent): it stopped on something unexpected, or the raw store isn't configured. Names the kind of error only.
- `Morning snapshot: raw store check failed` (urgent): R2 answered, but not correctly (wrong endpoint, bad keys). The snapshot still ran, so the day is saved, but the files may not be where ingest looks.
- `Snapshot job failed` (urgent, from the workflow itself): the job failed, timed out or was cancelled before the app could say why (a settings error, a failed install, a killed process). Tap it to open the run.

**Exit codes (M3):** a scheduled command exits **3** once it has sent its failure to the phone, and the workflow alerts on any other failure, so a failure is reported exactly once and never silently. A prod job without `FH_NTFY_TOPIC` goes red on purpose, after saving the day. Prod and the laptop use different topics (decided 2026-11-07).

**Ingest (`fh ingest`, by hand until M3):**
- `Ingest started`
- `Ingest: League data fetched` ("15 of 15 leagues.")
- `Ingest: Public data loaded` (games, projection rows, injury changes, box score rows)
- `Ingest: Leagues stored` ("15 of 15 leagues: … roster entries, … matchups, … transactions.")
- `Ingest: retrying (attempt 2 of 3)` (normal), only when a league or the run failed in a retryable way.
- Then one of:
  - `Ingest succeeded` (quiet): "Leagues: 15 of 15 loaded. Box scores: … Database 24 MB (5% of the free plan)."
  - `Ingest finished with warnings` (normal): every league loaded, plus counts that need a look. For example "2 rostered players unresolved in 1 leagues", "1 leagues score a setting with no box score stat yet", or the database over 60%.
  - `Ingest failed` (loud; urgent if no league loaded): leagues failed, grouped by platform and cause, with attempts. For example "2 Sleeper leagues failed: the source's servers erred, after 3 attempts".

**Daily run (`fh run daily`, by hand until M3; `--dry-run` saves nothing, `--open` opens the report):**
- `Daily run started`
- The three ingest steps: `Daily run: League data fetched`, `Daily run: Public data loaded`, `Daily run: Leagues stored`
- `Daily run: Box scores checked` ("ESPN season totals 155/155; Sleeper one game a week 405/417."): our box scores and scoring against each platform's own numbers, settled data only (ADR 0022). Below the expected agreement (ESPN 98%, Sleeper 90%), the run ends with a warning.
- `Daily run: Lock-or-pass calls built` ("8 calls in 1 of 1 lock-in leagues (8 newly logged).")
- `Daily run: Report built` ("10 cards in 1 reports.")
- `Daily run: retrying (attempt 2 of 3)` (normal), only when a league or the run failed in a retryable way. The run reruns in full: ingest is idempotent, and a call already logged is never logged again.
- Then one of:
  - `Daily run succeeded` (quiet): calls, leagues, box scores, database size.
  - `Daily run finished with warnings` (normal).
  - `Daily run failed` (loud; urgent if no report was built): leagues or lock-in calls that failed, by platform and cause.

The report itself, with the lock-or-pass calls, is a local page (`data/reports/<date>/daily.html`) until the phone app (M3) shows it. It never goes through ntfy.

**Weekly run (`fh run weekly`, by hand until M3; `--open` opens the overview):**
- `Weekly run started`
- The three ingest steps
- `Weekly run: Weekly overview built` ("3 of 3 leagues; 1 streaming moves; 2 calls newly logged."). Mid-week, a league whose banked points differ from the platform's daily totals by more than a point ends the run with a warning (a lineup or box score is off).
- `Weekly run: Report built`
- Retries as for the daily run.
- Then `succeeded` (quiet), `finished with warnings` (normal) or `failed` (loud).

The overview itself is a local page (`data/reports/<date>/weekly.html`) until the phone app shows it.

**Test:** `uv run fh doctor --alert` sends one test event.

The 06:30 and 17:30 jobs run the code in your main checkout, so the snapshot events start once the M2 branch is merged.

## Rules every event follows

- **Counts and statuses only:** never secrets, and never league data (league names, league IDs, leaguemates, team names). Anyone who knows the topic name can read it.
- **Built from numbers, not error text.** Phone messages come from structured outcomes (`FailureKind`, counts), never from exception or error strings, which can carry names or URLs with league IDs. Full error text goes to the terminal and the run log only.
- **Step names are fixed phrases** in code ("Box scores loaded"), never built from data. The `detail` carries the numbers.
- **One ending per run:** `succeeded`, `warned` or `failed`, never two.
- **Ops only:** if it's something the user should *do*, it belongs in the phone app's push, not here.

## Add an event to an existing run

Say the daily run should report when the lock-or-pass calls are built:

```python
# pipeline/daily.py
events.step("Lock-or-pass calls built", f"{len(calls)} calls across {n_leagues} leagues.")
```

Then:
1. **Test it.** Assert the title and priority with a fake notifier (see `tests/unit/test_events.py`, or the step assertions in `tests/integration/test_ingest.py`).
2. **Add it to "What you get today"** above.

## Add events (and retries) to a new run

```python
from fantasy_helper.domain.runs import RunKind
from fantasy_helper.observability.events import events_from_settings
from fantasy_helper.pipeline.retry import MAX_ATTEMPTS, classify_exception, outcome, wait_before


def daily(settings: Settings) -> None:
    events = events_from_settings(settings, RunKind.DAILY, label="Daily run")
    events.started()
    attempt = 1
    while True:
        try:
            report = run_daily(..., events=events)  # emits its own step events
            break
        except Exception as exc:
            kind = classify_exception(exc)
            if kind.retryable and attempt < MAX_ATTEMPTS:
                attempt += 1
                events.retrying(
                    attempt, MAX_ATTEMPTS, wait_before(attempt), f"Stopped: {kind.describe}."
                )
                time.sleep(wait_before(attempt))
                continue
            events.failed(f"Stopped: {outcome(kind, attempt)}.")  # the kind, never the text
            raise
    events.succeeded(f"{len(report.cards)} cards.")
```

- **Pipeline functions take `events: RunEvents | None = None`** and report their own steps (`run_ingest` is the model). The CLI command creates the events, calls `started()`, runs the retry loop and decides the ending.
- **Parts of a run** (datasets, leagues) record a `FailureKind` instead of raising, so the run can retry just those parts and count outcomes.
- **A new failure cause** gets a `FailureKind` member (with `retryable` and plain words in `domain/runs.py`) and a rule in `classify_exception`.
- **A new kind of run** needs a `RunKind` member in `domain/runs.py`.
- **Tests** pass a fake notifier, a clock and a sleep that records waits instead of sleeping (see `tests/unit/test_snapshot.py`).

## Settings

| Variable | What it does |
|---|---|
| `FH_NTFY_TOPIC` | Your private topic (secret). Generate with `uv run fh keygen --topic` and subscribe in the ntfy app. Unset: events go to the log only. |
| `FH_NTFY_SERVER` | `https://ntfy.sh` by default |
| `FH_NOTIFY_PROGRESS` | `true` by default. `false` keeps only retries, warnings and failures. |

From M3 the same variables live in the GitHub `prod` environment, and the scheduled jobs send the same events.

## Planned

| When | Event | Why |
|---|---|---|
| M2 (lock-in) | `warned` when box scores disagree with ESPN's or Sleeper's own numbers more than usual (the daily reconciliation, ADR 0022) | Watches the one box score truth |
| M3 | The same events from GitHub Actions, plus a workflow-level failure step for crashes before Python starts | The cloud runs. **Snapshots done in part 1** (also a `crashed` event for unexpected errors); the daily and weekly runs follow in part 2 |
| M3 | `failed(urgent)` if the report isn't published by 7:45 a.m. | The 8 a.m. promise |
| M3 | Missed run: a watchdog outside the runner (Healthchecks.io, free) that alerts if a scheduled run never started | A run that never starts can't report itself |
| M4 | AI step failed, so the report shipped engine-only; daily model cost over a cap | Brain and budget |
