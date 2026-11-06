"""Daily snapshots: save what changes every day, before it's gone (ADR 0014).

Projections and injury statuses are only available as they stand right now, and grades and
timeline graphs need them exactly as they stood on each day. Two runs a day:

- morning: the full set, including Sleeper's player list (Sleeper asks for at most one a day)
- evening: what moves before tip-off (ESPN injury statuses and projections, Sleeper's week)

Every response is stored raw and untouched. A dataset that fails or looks wrong raises an
ops alert; the others still run. The raw files are written even if the database is down.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

import httpx

from fantasy_helper.adapters import _http
from fantasy_helper.adapters._http import RawResponse
from fantasy_helper.adapters.espn import public as espn
from fantasy_helper.adapters.sleeper import public as sleeper
from fantasy_helper.domain.calendar import season_end_year, season_start_year
from fantasy_helper.domain.ports import Alert, AlertPriority, Clock, Notifier, SystemClock
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus, SourceFreshness
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.storage.raw_store import RawStore, RawStoreError

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
        "espn", "pro_schedule", MORNING_ONLY,
        lambda c, ctx: espn.pro_schedule(c, ctx.espn_season),
        espn.validate_schedule, espn.item_count,
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
) -> SnapshotResult:
    clock = clock or SystemClock()
    started = clock.now()
    if not _wait_for_network(client, network_wait_seconds):
        log.warning("snapshot_network_unreachable", waited_seconds=network_wait_seconds)

    state_result, state_data = _capture(STATE, client, _NO_CONTEXT, store, run_set)
    ctx = _context(state_data if state_result.ok else None, started.date())
    results = [state_result]
    for dataset in DATASETS:
        if run_set in dataset.sets:
            results.append(_capture(dataset, client, ctx, store, run_set)[0])

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
        errors=tuple(f"{r.label}: {r.error}" for r in failures),
    )

    recorded, record_error = _record(record, run)
    if failures or record_error:
        _alert(notifier, run_set, results, failures, record_error)
    log.info("snapshot_finished", run_set=run_set.value, status=status.value, failed=len(failures))
    return SnapshotResult(run_set=run_set, run=run, results=tuple(results), recorded=recorded)


def _capture(
    dataset: Dataset,
    client: httpx.Client,
    ctx: SnapshotContext,
    store: RawStore,
    run_set: SnapshotSet,
) -> tuple[DatasetResult, Any]:
    def failed(error: str, response: RawResponse | None = None) -> tuple[DatasetResult, Any]:
        log.warning("snapshot_dataset_failed", dataset=dataset.label, error=error)
        fetched = response.fetched_at if response else None
        return DatasetResult(dataset.label, None, 0, 0, fetched, error), None

    try:
        response = dataset.fetch(client, ctx)
    except MissingContextError as exc:
        return failed(f"skipped: {exc}")
    except httpx.HTTPError as exc:
        return failed(f"request failed: {type(exc).__name__}")
    if not response.ok:
        return failed(f"HTTP {response.status_code}", response)

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
    except (RawStoreError, OSError) as exc:
        return failed(f"could not save: {exc}", response)
    return (
        DatasetResult(dataset.label, key, items, len(response.body), response.fetched_at, problem),
        data,
    )


def _context(state: Any, today: date) -> SnapshotContext:
    season = str(state["season"]) if state else str(season_start_year(today))
    week = int(state["week"]) if state else None
    return SnapshotContext(
        espn_season=season_end_year(today), sleeper_season=season, sleeper_week=week
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


def _alert(
    notifier: Notifier,
    run_set: SnapshotSet,
    results: list[DatasetResult],
    failures: list[DatasetResult],
    record_error: str | None,
) -> None:
    lines = [f"{r.label}: {r.error}" for r in failures]
    if record_error:
        lines.append(record_error)
    all_failed = len(failures) == len(results)
    title = f"{run_set.value.title()} snapshot " + ("failed" if all_failed else "incomplete")
    summary = f"{len(failures)} of {len(results)} datasets failed." if failures else "Saved."
    try:
        notifier.send(
            Alert(
                title=title,
                message=(summary + "\n" + "\n".join(lines))[:2000],
                priority=AlertPriority.URGENT if all_failed else AlertPriority.HIGH,
                tags=("warning",),
            )
        )
    except Exception as exc:
        log.error("snapshot_alert_failed", error=type(exc).__name__)
