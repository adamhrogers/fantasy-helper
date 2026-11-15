"""Scheduled runs wait for this morning's snapshot, not build from yesterday's (ADR 0026)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fantasy_helper.pipeline.freshness import StaleSnapshotError, require_snapshot_today
from fantasy_helper.storage.raw_store import LocalRawStore

MORNING_RUN = datetime(2026, 11, 15, 11, 45, tzinfo=UTC)  # 06:45 Eastern


def store_with_box_scores(root: Path, *fetched: datetime) -> LocalRawStore:
    store = LocalRawStore(root)
    for at in fetched:
        store.put(source="espn", dataset="game_logs", fetched_at=at, body=b"{}", meta={})
    return store


def test_this_mornings_snapshot_is_enough(tmp_path: Path) -> None:
    store = store_with_box_scores(tmp_path, datetime(2026, 11, 15, 11, 17, tzinfo=UTC))
    require_snapshot_today(store, MORNING_RUN)


def test_yesterdays_is_not(tmp_path: Path) -> None:
    store = store_with_box_scores(tmp_path, datetime(2026, 11, 14, 11, 17, tzinfo=UTC))
    with pytest.raises(StaleSnapshotError, match="2026-11-14, not 2026-11-15"):
        require_snapshot_today(store, MORNING_RUN)


def test_no_snapshot_at_all_is_not(tmp_path: Path) -> None:
    with pytest.raises(StaleSnapshotError, match="no box-score snapshot yet"):
        require_snapshot_today(LocalRawStore(tmp_path), MORNING_RUN)


def test_only_the_newest_counts(tmp_path: Path) -> None:
    store = store_with_box_scores(
        tmp_path,
        datetime(2026, 11, 14, 11, 17, tzinfo=UTC),
        datetime(2026, 11, 15, 11, 17, tzinfo=UTC),
    )
    require_snapshot_today(store, MORNING_RUN)


def test_the_day_is_the_nba_days_not_utcs(tmp_path: Path) -> None:
    """A run at 21:30 Eastern is already tomorrow in UTC; the snapshot from that morning still
    counts as today's (the NBA's calendar, like every loader)."""
    store = store_with_box_scores(tmp_path, datetime(2026, 11, 15, 11, 17, tzinfo=UTC))
    require_snapshot_today(store, datetime(2026, 11, 16, 2, 30, tzinfo=UTC))
