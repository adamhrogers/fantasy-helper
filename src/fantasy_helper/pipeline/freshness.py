"""A scheduled run must work from this morning's data, not quietly from yesterday's (ADR 0026).

The morning snapshot saves box scores (`espn/game_logs`) once a day, before the daily and weekly
runs start. If it didn't, a run that went ahead would build reports and log calls from stale box
scores. So the scheduled runs check first, and wait: a snapshot that is a few minutes late is
found by the retry, and one that never comes fails the run loudly.
"""

from datetime import datetime

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.storage.raw_store import RawStore, fetched_at_from_key

SNAPSHOT_DATASET = ("espn", "game_logs")


class StaleSnapshotError(Exception):
    """The newest morning snapshot is older than today (the NBA's Eastern day)."""


def require_snapshot_today(store: RawStore, now: datetime) -> None:
    today = now.astimezone(NBA_TIMEZONE).date()
    keys = store.keys(*SNAPSHOT_DATASET)
    if keys:
        newest = fetched_at_from_key(keys[-1]).astimezone(NBA_TIMEZONE).date()
        if newest >= today:
            return
        raise StaleSnapshotError(f"the newest box-score snapshot is from {newest}, not {today}")
    raise StaleSnapshotError(f"no box-score snapshot yet, and it's {today}")
