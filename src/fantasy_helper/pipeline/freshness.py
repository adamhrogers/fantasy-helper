"""The morning runs start only from a complete morning snapshot (ADR 0026, 0028).

The morning snapshot writes a small marker to the raw store **only when every dataset of the set
saved and passed its checks**. A dataset that failed, or that was saved but flagged as suspicious
(a half-empty projections file, say), means no marker, so nothing downstream builds from it. The
daily and weekly runs look for today's marker first, and wait for it (the existing retries) if a
hand-started run is early.

A marker rather than "does today's file exist": a flagged payload is kept as evidence, so it
exists, and looks fresh, and is wrong.
"""

import json
from datetime import datetime

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.storage.raw_store import RawStore, fetched_at_from_key

MARKER = ("fh", "snapshot_complete")


class StaleSnapshotError(Exception):
    """There's no complete morning snapshot from today (the NBA's Eastern day) yet."""


def mark_snapshot_complete(
    store: RawStore, *, run_set: str, datasets: list[str], at: datetime
) -> str:
    """Record that every dataset of `run_set` saved and passed. Call it only then."""
    body = json.dumps({"set": run_set, "datasets": sorted(datasets)}).encode()
    return store.put(
        source=MARKER[0], dataset=MARKER[1], fetched_at=at, body=body, meta={"set": run_set}
    )


def require_snapshot_today(store: RawStore, now: datetime) -> None:
    today = now.astimezone(NBA_TIMEZONE).date()
    keys = store.keys(*MARKER)
    if keys:
        newest = fetched_at_from_key(keys[-1]).astimezone(NBA_TIMEZONE).date()
        if newest >= today:
            return
        raise StaleSnapshotError(
            f"the last complete morning snapshot is from {newest}, not {today}"
        )
    raise StaleSnapshotError(f"no complete morning snapshot yet, and it's {today}")
