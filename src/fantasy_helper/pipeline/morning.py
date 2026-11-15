"""When is the morning snapshot complete enough to build the day's reports from (ADR 0028)?

Only when **every** dataset of the morning set saved and passed its checks. Not "mostly": a daily
run decides lock or pass from projections and box scores, and a call in the registry can never be
edited, so a half-fresh input isn't worth a report. If it isn't complete, nothing downstream runs
and the snapshot's own alert says what failed.
"""

from fantasy_helper.domain.runs import RunStatus
from fantasy_helper.pipeline.snapshot import SnapshotResult, SnapshotSet
from fantasy_helper.storage.raw_store import RawStore, RawStoreError


def incompleteness(result: SnapshotResult, store: RawStore) -> str | None:
    """None when the morning set is complete; otherwise the reason, in a few words (counts and
    kinds only: this goes to the phone)."""
    if result.run_set is not SnapshotSet.MORNING:
        return "not the morning set"
    if result.run.status is not RunStatus.SUCCEEDED:
        failed = sum(1 for r in result.results if not r.ok)
        return f"{failed} of {len(result.results)} datasets failed"
    for r in result.results:
        if r.reused and r.key is not None and not _was_valid(store, r.key):
            # Saved earlier today by another run, and flagged then: it wasn't fetched again, so
            # this run's "succeeded" says nothing about it. Look at what was saved.
            return f"{r.label} was saved earlier today and flagged"
    return None


def _was_valid(store: RawStore, key: str) -> bool:
    try:
        return bool(store.meta(key).get("validation") == "ok")
    except (RawStoreError, OSError, KeyError, ValueError):
        return False
