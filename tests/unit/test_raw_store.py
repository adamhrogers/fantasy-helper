import gzip
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fantasy_helper.storage.raw_store import LocalRawStore, RawStoreError, make_key

AT = datetime(2026, 11, 6, 11, 30, 5, 123456, tzinfo=UTC)


def test_round_trip_and_sidecar(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    body = b'{"players": []}'
    key = store.put(source="espn", dataset="players", fetched_at=AT, body=body, meta={"x": 1})
    assert key == "espn/players/2026-11-06/113005123456Z"
    assert store.get(key) == body
    meta = store.meta(key)
    assert meta["sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["bytes"] == len(body)
    assert meta["x"] == 1
    assert gzip.decompress((tmp_path / f"{key}.json.gz").read_bytes()) == body


def test_snapshots_are_never_overwritten(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    store.put(source="espn", dataset="players", fetched_at=AT, body=b"1", meta={})
    with pytest.raises(RawStoreError, match="overwrite"):
        store.put(source="espn", dataset="players", fetched_at=AT, body=b"2", meta={})
    key = make_key("espn", "players", AT)
    assert store.get(key) == b"1"


def test_keys_use_the_utc_date() -> None:
    late_evening_eastern = datetime(2026, 11, 6, 21, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert make_key("espn", "players", late_evening_eastern).startswith("espn/players/2026-11-07/")


def test_naive_times_are_rejected() -> None:
    with pytest.raises(RawStoreError):
        make_key("espn", "players", datetime(2026, 11, 6))  # noqa: DTZ001
