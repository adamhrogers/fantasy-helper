"""When the morning snapshot is complete enough to build the day's reports from (ADR 0028)."""

from datetime import UTC, datetime
from pathlib import Path

from fantasy_helper.domain.runs import FailureKind, RunKind, RunRecord, RunStatus
from fantasy_helper.pipeline.morning import incompleteness
from fantasy_helper.pipeline.snapshot import DatasetResult, SnapshotResult, SnapshotSet
from fantasy_helper.storage.raw_store import LocalRawStore

NOW = datetime(2026, 11, 15, 11, 17, tzinfo=UTC)


def result(*datasets: DatasetResult, run_set: SnapshotSet = SnapshotSet.MORNING) -> SnapshotResult:
    failed = [d for d in datasets if not d.ok]
    status = (
        RunStatus.SUCCEEDED if not failed
        else RunStatus.FAILED if len(failed) == len(datasets)
        else RunStatus.DEGRADED
    )  # fmt: skip
    run = RunRecord(kind=RunKind.SNAPSHOT, status=status, started_at=NOW, ended_at=NOW)
    return SnapshotResult(run_set=run_set, run=run, results=datasets, recorded=False)


def saved(label: str, key: str = "k") -> DatasetResult:
    return DatasetResult(label, key, 10, 100, NOW, None)


def failed(label: str) -> DatasetResult:
    return DatasetResult(label, None, 0, 0, NOW, "HTTP 500", FailureKind.SERVER_ERROR)


def test_every_dataset_saved_and_valid_is_complete(tmp_path: Path) -> None:
    assert incompleteness(result(saved("a"), saved("b")), LocalRawStore(tmp_path)) is None


def test_one_failed_dataset_means_not_complete(tmp_path: Path) -> None:
    why = incompleteness(result(saved("a"), failed("b"), saved("c")), LocalRawStore(tmp_path))
    assert why == "1 of 3 datasets failed"  # counts only: this goes to the phone


def test_a_flagged_dataset_is_a_failed_one(tmp_path: Path) -> None:
    """A payload that looked wrong is saved as evidence but counts as failed (the snapshot's
    rule), so a half-empty projections file can't start the daily run."""
    flagged = DatasetResult(
        "sleeper.projections_season", "k", 5, 50, NOW, "only 588 of 2132 rows have stats",
        FailureKind.BAD_RESPONSE,
    )  # fmt: skip
    assert incompleteness(result(saved("a"), flagged), LocalRawStore(tmp_path)) is not None


def test_the_evening_set_never_completes_the_morning(tmp_path: Path) -> None:
    why = incompleteness(result(saved("a"), run_set=SnapshotSet.EVENING), LocalRawStore(tmp_path))
    assert why == "not the morning set"


def reused(store: LocalRawStore, validation: str) -> DatasetResult:
    key = store.put(
        source="sleeper", dataset="players", fetched_at=NOW, body=b"[]",
        meta={"validation": validation},
    )  # fmt: skip
    return DatasetResult("sleeper.players", key, 0, 0, None, None, reused=True)


def test_a_dataset_saved_earlier_and_flagged_then_is_not_trusted_now(tmp_path: Path) -> None:
    """Sleeper's player list is fetched once a day, so a later run reuses it and reports success
    for it without looking. If the first run flagged it, this one must not hide that."""
    store = LocalRawStore(tmp_path)
    why = incompleteness(result(saved("a"), reused(store, "only 10 players")), store)
    assert why == "sleeper.players was saved earlier today and flagged"


def test_a_dataset_saved_earlier_and_valid_is_fine(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    assert incompleteness(result(saved("a"), reused(store, "ok")), store) is None


def test_a_reused_dataset_we_cant_read_back_is_not_trusted(tmp_path: Path) -> None:
    gone = DatasetResult("sleeper.players", "sleeper/players/2026-11-15/x", 0, 0, None, None,
                         reused=True)  # fmt: skip
    why = incompleteness(result(saved("a"), gone), LocalRawStore(tmp_path))
    assert why == "sleeper.players was saved earlier today and flagged"
