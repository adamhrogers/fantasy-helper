"""The morning runs start only from a complete morning snapshot (ADR 0028)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fantasy_helper.pipeline.freshness import (
    StaleSnapshotError,
    mark_snapshot_complete,
    require_snapshot_today,
)
from fantasy_helper.storage.raw_store import LocalRawStore

MORNING_RUN = datetime(2026, 11, 15, 11, 45, tzinfo=UTC)  # 06:45 Eastern


def store_with_markers(root: Path, *at: datetime) -> LocalRawStore:
    store = LocalRawStore(root)
    for moment in at:
        mark_snapshot_complete(store, run_set="morning", datasets=["espn.game_logs"], at=moment)
    return store


def test_a_complete_snapshot_from_this_morning_is_enough(tmp_path: Path) -> None:
    store = store_with_markers(tmp_path, datetime(2026, 11, 15, 11, 17, tzinfo=UTC))
    require_snapshot_today(store, MORNING_RUN)


def test_yesterdays_marker_is_not(tmp_path: Path) -> None:
    store = store_with_markers(tmp_path, datetime(2026, 11, 14, 11, 17, tzinfo=UTC))
    with pytest.raises(StaleSnapshotError, match="2026-11-14, not 2026-11-15"):
        require_snapshot_today(store, MORNING_RUN)


def test_no_marker_at_all_is_not(tmp_path: Path) -> None:
    with pytest.raises(StaleSnapshotError, match="no complete morning snapshot yet"):
        require_snapshot_today(LocalRawStore(tmp_path), MORNING_RUN)


def test_box_scores_alone_are_not_a_complete_snapshot(tmp_path: Path) -> None:
    """The old check looked only at box scores: a snapshot that saved those but failed on the
    projections would have passed. Only the marker, written when everything passed, counts."""
    store = LocalRawStore(tmp_path)
    store.put(
        source="espn", dataset="game_logs", fetched_at=datetime(2026, 11, 15, 11, 17, tzinfo=UTC),
        body=b"{}", meta={"validation": "ok"},
    )  # fmt: skip
    with pytest.raises(StaleSnapshotError):
        require_snapshot_today(store, MORNING_RUN)


def test_only_the_newest_marker_counts(tmp_path: Path) -> None:
    store = store_with_markers(
        tmp_path,
        datetime(2026, 11, 14, 11, 17, tzinfo=UTC),
        datetime(2026, 11, 15, 11, 17, tzinfo=UTC),
    )
    require_snapshot_today(store, MORNING_RUN)


def test_the_day_is_the_nba_days_not_utcs(tmp_path: Path) -> None:
    """A run at 21:30 Eastern is already tomorrow in UTC; this morning's snapshot still counts as
    today's (the NBA's calendar, like every loader)."""
    store = store_with_markers(tmp_path, datetime(2026, 11, 15, 11, 17, tzinfo=UTC))
    require_snapshot_today(store, datetime(2026, 11, 16, 2, 30, tzinfo=UTC))


def test_the_marker_says_which_datasets_were_complete(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    key = mark_snapshot_complete(
        store, run_set="morning", datasets=["sleeper.state", "espn.season"],
        at=datetime(2026, 11, 15, 11, 17, tzinfo=UTC),
    )  # fmt: skip
    assert json.loads(store.get(key)) == {
        "set": "morning",
        "datasets": ["espn.season", "sleeper.state"],
    }
