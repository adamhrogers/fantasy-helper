"""Daily snapshots: save what changes every day, before it's gone (ADR 0014).

Projections and injury statuses are only available as they stand right now, and grades and
timeline graphs need them exactly as they stood on each day. Two runs a day:

- morning: the full set, including Sleeper's player list (Sleeper asks for at most one a day)
- evening: what moves before tip-off (ESPN injury statuses and projections, Sleeper's week)

Box scores (`espn.game_logs`, morning) are the exception: they never change, so a missed day
can be recovered later with a deeper pull (`fh snapshot --only espn.game_logs --game-log-depth`).

Every response is stored raw and untouched. A dataset that fails or looks wrong makes the run
fail loudly on the phone (run events, docs/runbooks/run-events.md); the others still run. The
raw files are written even if the database is down.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

import httpx

from fantasy_helper.adapters import _http
from fantasy_helper.adapters._http import RawResponse
from fantasy_helper.adapters.espn import public as espn
from fantasy_helper.adapters.sleeper import public as sleeper
from fantasy_helper.domain.calendar import season_end_year, season_start_year
from fantasy_helper.domain.ports import Clock, Notifier, SystemClock
from fantasy_helper.domain.runs import FailureKind, RunKind, RunRecord, RunStatus, SourceFreshness
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.pipeline.retry import (
    MAX_ATTEMPTS,
    RETRY_WAITS_SECONDS,
    classify_exception,
    classify_status,
    outcome,
    wait_before,
)
from fantasy_helper.storage.raw_store import RawStore, RawStoreError, RawStoreUnavailableError

log = get_logger(__name__)


class SnapshotSet(StrEnum):
    MORNING = "morning"
    EVENING = "evening"


BOTH = frozenset(SnapshotSet)
MORNING_ONLY = frozenset({SnapshotSet.MORNING})


class MissingContextError(Exception):
    """A dataset needs something an earlier request was supposed to provide."""


@dataclass(frozen=True)
class SnapshotContext:
    espn_season: int  # 2027 for 2026-27
    sleeper_season: str  # "2026" for 2026-27
    sleeper_week: int | None
    game_log_depth: int = espn.GAME_LOG_DEPTH


_NO_CONTEXT = SnapshotContext(espn_season=0, sleeper_season="", sleeper_week=None)


@dataclass(frozen=True)
class Dataset:
    source: str
    name: str
    sets: frozenset[SnapshotSet]
    fetch: Callable[[httpx.Client, SnapshotContext], RawResponse]
    validate: Callable[[Any], str | None]
    count: Callable[[Any], int]

    @property
    def label(self) -> str:
        return f"{self.source}.{self.name}"


def _week(ctx: SnapshotContext) -> int:
    if ctx.sleeper_week is None:
        raise MissingContextError("Sleeper week unknown (state request failed)")
    return ctx.sleeper_week


STATE = Dataset(
    "sleeper", "state", BOTH,
    lambda c, _: sleeper.state(c), sleeper.validate_state, sleeper.item_count,
)  # fmt: skip
DATASETS: tuple[Dataset, ...] = (
    Dataset(
        "espn", "season", BOTH,
        lambda c, ctx: espn.season_info(c, ctx.espn_season), espn.validate_season, espn.item_count,
    ),
    Dataset(
        "espn", "players_projections", BOTH,
        lambda c, ctx: espn.players_projections(c, ctx.espn_season),
        espn.validate_players, espn.item_count,
    ),
    Dataset(
        "espn", "game_logs", MORNING_ONLY,
        lambda c, ctx: espn.game_logs(c, ctx.espn_season, games=ctx.game_log_depth),
        espn.validate_game_logs, espn.item_count,
    ),
    Dataset(
        "espn", "pro_schedule", MORNING_ONLY,
        lambda c, ctx: espn.pro_schedule(c, ctx.espn_season),
        espn.validate_schedule, espn.item_count,
    ),
    Dataset(
        "espn", "athletes", MORNING_ONLY,
        lambda c, _: espn.athletes(c), espn.validate_athletes, espn.item_count,
    ),
    Dataset(
        "sleeper", "players", MORNING_ONLY,
        lambda c, _: sleeper.players(c), sleeper.validate_players, sleeper.item_count,
    ),
    Dataset(
        "sleeper", "projections_season", MORNING_ONLY,
        lambda c, ctx: sleeper.season_projections(c, ctx.sleeper_season),
        sleeper.validate_projections, sleeper.item_count,
    ),
    Dataset(
        "sleeper", "projections_week", BOTH,
        lambda c, ctx: sleeper.week_projections(c, ctx.sleeper_season, _week(ctx)),
        sleeper.validate_projections, sleeper.item_count,
    ),
)  # fmt: skip


@dataclass(frozen=True)
class DatasetResult:
    label: str
    key: str | None
    items: int
    bytes: int
    fetched_at: datetime | None
    error: str | None
    failure: FailureKind | None = None  # why it failed, and whether a retry can help
    attempts: int = 1
    reused: bool = False  # a once-a-day dataset another run already saved today

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class SnapshotResult:
    run_set: SnapshotSet
    run: RunRecord
    results: tuple[DatasetResult, ...]
    recorded: bool


def run_snapshot(
    run_set: SnapshotSet,
    *,
    client: httpx.Client,
    store: RawStore,
    notifier: Notifier,
    record: Callable[[RunRecord], None] | None,
    clock: Clock | None = None,
    network_wait_seconds: float = 120.0,
    only: frozenset[str] | None = None,
    game_log_depth: int = espn.GAME_LOG_DEPTH,
    progress: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    retry_waits: tuple[float, ...] = RETRY_WAITS_SECONDS,
) -> SnapshotResult:
    """Capture the set's datasets. `only` limits the run to those dataset labels (Sleeper's
    state is always fetched, for context); use it to backfill, never to skip a scheduled run.

    Datasets that fail in a way a retry can fix run again, alone, up to MAX_ATTEMPTS in all
    (pipeline.retry). Datasets that succeeded are never fetched twice, and neither is a
    once-a-day dataset that was saved but flagged: Sleeper asks for its player list at most once
    a day."""
    clock = clock or SystemClock()
    started = clock.now()
    events = RunEvents(
        RunKind.SNAPSHOT,
        notifier,
        label=f"{run_set.value.title()} snapshot",
        progress=progress,
        clock=clock,
    )
    planned = 1 + sum(
        1 for d in DATASETS if run_set in d.sets and (only is None or d.label in only)
    )
    events.started(f"{planned} datasets to save.")
    if not _wait_for_network(client, network_wait_seconds):
        log.warning("snapshot_network_unreachable", waited_seconds=network_wait_seconds)

    chosen = [d for d in DATASETS if run_set in d.sets and (only is None or d.label in only)]
    state_result, state_data = _capture(STATE, client, _NO_CONTEXT, store, run_set)
    ctx = _context(state_data if state_result.ok else None, started.date(), game_log_depth)
    by_label: dict[str, DatasetResult] = {STATE.label: state_result}
    today = started.astimezone(UTC).date()
    for dataset in chosen:
        by_label[dataset.label] = (
            _saved_today(dataset, store, today) or _capture(dataset, client, ctx, store, run_set)[0]
        )

    for attempt in range(2, MAX_ATTEMPTS + 1):
        retry = _retryable(by_label)
        if not retry:
            break
        wait = wait_before(attempt, retry_waits)
        events.retrying(
            attempt,
            MAX_ATTEMPTS,
            wait,
            f"{len(retry)} of {len(by_label)} datasets failed in a way a retry can fix.",
            [f"{label}: {by_label[label].failure.describe}" for label in retry  # type: ignore[union-attr]
             ],
        )  # fmt: skip
        sleep(wait)
        if STATE.label in retry:
            state_result, state_data = _capture(STATE, client, _NO_CONTEXT, store, run_set)
            by_label[STATE.label] = replace(state_result, attempts=attempt)
            if state_result.ok:
                ctx = _context(state_data, started.date(), game_log_depth)
        for dataset in chosen:
            if dataset.label in retry:
                result = _capture(dataset, client, ctx, store, run_set)[0]
                by_label[dataset.label] = replace(result, attempts=attempt)

    results = list(by_label.values())
    failures = [r for r in results if not r.ok]
    if not failures:
        status = RunStatus.SUCCEEDED
    elif len(failures) == len(results):
        status = RunStatus.FAILED
    else:
        status = RunStatus.DEGRADED
    run = RunRecord(
        kind=RunKind.SNAPSHOT,
        status=status,
        started_at=started,
        ended_at=clock.now(),
        sources=tuple(
            SourceFreshness(source=r.label, pulled_at=r.fetched_at, rows=r.items)
            for r in results
            if r.key is not None and r.fetched_at is not None
        ),
        rows_loaded=sum(r.items for r in results if r.key is not None),
        errors=tuple(f"{r.label}: {r.error} (attempts: {r.attempts})" for r in failures),
    )

    recorded, record_error = _record(record, run)
    _report(events, results, failures, record_error)
    log.info("snapshot_finished", run_set=run_set.value, status=status.value, failed=len(failures))
    return SnapshotResult(run_set=run_set, run=run, results=tuple(results), recorded=recorded)


# Sources that ask for at most one request a day: once saved, never fetched again that run,
# even if the response looked suspicious (it's kept, and flagged, as evidence).
ONCE_A_DAY = frozenset({"sleeper.players"})


def _saved_today(dataset: Dataset, store: RawStore, today: date) -> DatasetResult | None:
    """A once-a-day dataset that another run (the laptop or the cloud) already saved today
    isn't fetched again. If the store can't tell, the dataset is fetched as usual."""
    if dataset.label not in ONCE_A_DAY:
        return None
    try:
        saved = store.keys_on(dataset.source, dataset.name, today)
    except (RawStoreError, OSError) as exc:
        log.warning("snapshot_once_a_day_check_failed", dataset=dataset.label, error=str(exc))
        return None
    if not saved:
        return None
    log.info("snapshot_dataset_already_saved", dataset=dataset.label, key=saved[-1])
    return DatasetResult(dataset.label, saved[-1], 0, 0, None, None, reused=True)


def _retryable(by_label: dict[str, DatasetResult]) -> list[str]:
    """Labels worth another try. A dataset that only lacked Sleeper's state is retried only if
    the state itself is being retried; a once-a-day dataset that was saved never is."""
    state = by_label[STATE.label]
    state_retrying = state.failure is not None and state.failure.retryable
    return [
        label
        for label, r in by_label.items()
        if r.failure is not None
        and r.failure.retryable
        and (r.failure is not FailureKind.MISSING_INPUT or state_retrying)
        and not (label in ONCE_A_DAY and r.key is not None)
    ]


def _capture(
    dataset: Dataset,
    client: httpx.Client,
    ctx: SnapshotContext,
    store: RawStore,
    run_set: SnapshotSet,
) -> tuple[DatasetResult, Any]:
    def failed(
        error: str, kind: FailureKind, response: RawResponse | None = None
    ) -> tuple[DatasetResult, Any]:
        log.warning("snapshot_dataset_failed", dataset=dataset.label, error=error, kind=kind.value)
        fetched = response.fetched_at if response else None
        return DatasetResult(dataset.label, None, 0, 0, fetched, error, kind), None

    try:
        response = dataset.fetch(client, ctx)
    except MissingContextError as exc:
        return failed(f"skipped: {exc}", FailureKind.MISSING_INPUT)
    except httpx.HTTPError as exc:
        return failed(f"request failed: {type(exc).__name__}", classify_exception(exc))
    if not response.ok:
        return failed(
            f"HTTP {response.status_code}", classify_status(response.status_code), response
        )

    try:
        data: Any = json.loads(response.body)
        problem = dataset.validate(data)
        items = dataset.count(data)
    except ValueError:
        data, problem, items = None, "response is not JSON", 0
    try:
        key = store.put(
            source=dataset.source,
            dataset=dataset.name,
            fetched_at=response.fetched_at,
            body=response.body,
            meta={
                "url": response.url,
                "params": dict(response.params),
                "headers": dict(response.headers),
                "status_code": response.status_code,
                "attempts": response.attempts,
                "items": items,
                "validation": problem or "ok",
                "snapshot_set": run_set.value,
            },
        )
    except RawStoreUnavailableError as exc:
        # Worth a retry, even for a once-a-day dataset: nothing was saved.
        return failed(f"could not save: {exc}", FailureKind.STORAGE_DOWN, response)
    except (RawStoreError, OSError) as exc:
        return failed(f"could not save: {exc}", FailureKind.STORAGE, response)
    # A suspicious response is saved (it's evidence of what changed) but counts as failed: it
    # may be a partial outage, so it's worth one more look.
    kind = FailureKind.BAD_RESPONSE if problem else None
    return (
        DatasetResult(
            dataset.label, key, items, len(response.body), response.fetched_at, problem, kind
        ),
        data,
    )


def _context(state: Any, today: date, game_log_depth: int) -> SnapshotContext:
    season = str(state["season"]) if state else str(season_start_year(today))
    week = int(state["week"]) if state else None
    return SnapshotContext(
        espn_season=season_end_year(today),
        sleeper_season=season,
        sleeper_week=week,
        game_log_depth=game_log_depth,
    )


def _wait_for_network(client: httpx.Client, seconds: float) -> bool:
    """After the laptop wakes, Wi-Fi can take a moment. Any HTTP response means we're online."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            client.head(f"{sleeper.API}/state/nba", timeout=5)
            return True
        except httpx.TransportError:
            if time.monotonic() >= deadline:
                return False
            _http._sleep(5)


def _record(record: Callable[[RunRecord], None] | None, run: RunRecord) -> tuple[bool, str | None]:
    if record is None:
        return False, None
    try:
        record(run)
    except Exception as exc:
        log.error("snapshot_run_log_failed", error=type(exc).__name__)
        return False, f"run log not written ({type(exc).__name__})"
    return True, None


def _report(
    events: RunEvents,
    results: list[DatasetResult],
    failures: list[DatasetResult],
    record_error: str | None,
) -> None:
    saved = sum(r.bytes for r in results if r.key is not None)
    if not failures and not record_error:
        reused = sum(1 for r in results if r.reused)
        note = f" {reused} already saved today by another run." if reused else ""
        events.succeeded(
            f"{len(results)}/{len(results)} datasets saved, {saved / 1e6:.1f} MB.{note}"
        )
        return
    lines = [
        f"{r.label}: {r.error}; {outcome(r.failure or FailureKind.BUG, r.attempts)}"
        for r in failures
    ]
    if record_error:
        lines.append(record_error)
    all_failed = len(failures) == len(results)
    summary = f"{len(failures)} of {len(results)} datasets failed." if failures else "Saved."
    events.failed(
        summary,
        lines,
        title=f"{events.label} " + ("failed" if all_failed else "incomplete"),
        urgent=all_failed,
    )
