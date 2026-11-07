"""The R2 raw store: same bytes as the local store, never overwrites, safe error messages."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError

from fantasy_helper.config import Settings
from fantasy_helper.storage.r2_store import PROBE_NAME, R2RawStore, r2_store_from_settings
from fantasy_helper.storage.raw_store import (
    LocalRawStore,
    RawStoreConflictError,
    RawStoreError,
    RawStoreUnavailableError,
    raw_store_from_settings,
)
from tests.fake_s3 import FakeS3, client_error, fake_store

AT = datetime(2026, 11, 6, 11, 30, 5, 123456, tzinfo=UTC)
BODY = b'{"players": [1, 2, 3]}'


def test_round_trip() -> None:
    store, _ = fake_store()
    key = store.put(source="espn", dataset="players", fetched_at=AT, body=BODY, meta={"x": 1})
    assert key == "espn/players/2026-11-06/113005123456Z"
    assert store.get(key) == BODY
    assert store.meta(key)["x"] == 1
    assert store.meta(key)["fetched_at"] == AT.isoformat()


def test_objects_are_byte_identical_to_the_local_store(tmp_path: Path) -> None:
    """So a laptop backlog uploaded with `fh raw upload` is indistinguishable from R2's own."""
    store, s3 = fake_store()
    local = LocalRawStore(tmp_path)
    for target in (store, local):
        target.put(source="espn", dataset="players", fetched_at=AT, body=BODY, meta={"x": 1})
    for suffix in (".json.gz", ".meta.json"):
        name = f"espn/players/2026-11-06/113005123456Z{suffix}"
        assert s3.objects[name] == (tmp_path / name).read_bytes()
    assert s3.content_types["espn/players/2026-11-06/113005123456Z.json.gz"] == "application/gzip"


def test_the_sidecar_is_written_before_the_payload() -> None:
    store, s3 = fake_store()
    store.put(source="espn", dataset="players", fetched_at=AT, body=BODY, meta={})
    assert [name.rsplit(".", 2)[-2:] for name in s3.puts] == [["meta", "json"], ["json", "gz"]]


def test_never_overwrites_an_existing_payload() -> None:
    store, s3 = fake_store()
    store.put(source="espn", dataset="players", fetched_at=AT, body=b"1", meta={})
    with pytest.raises(RawStoreError, match="overwrite"):
        store.put(source="espn", dataset="players", fetched_at=AT, body=b"2", meta={})
    assert store.get("espn/players/2026-11-06/113005123456Z") == b"1"
    assert len(s3.puts) == 2  # the check before writing stopped it; no third put was sent


def test_a_racing_writer_is_refused_by_the_conditional_write() -> None:
    """HEAD said absent (another machine wrote in between), so If-None-Match is the guard."""
    store, s3 = fake_store()
    store.put(source="espn", dataset="players", fetched_at=AT, body=b"1", meta={})
    s3.hidden_from_head = set(s3.objects)
    with pytest.raises(RawStoreError, match="overwrite"):
        store.put(source="espn", dataset="players", fetched_at=AT, body=b"2", meta={})
    assert store.get("espn/players/2026-11-06/113005123456Z") == b"1"


def test_keys_page_through_and_skip_nested_datasets() -> None:
    store, _ = fake_store(FakeS3(page_size=2))
    times = [datetime(2026, 11, d, 11, 30, tzinfo=UTC) for d in (8, 6, 7)]
    for at in times:
        store.put(source="espn", dataset="leagues/1/roster", fetched_at=at, body=BODY, meta={})
    store.put(source="espn", dataset="leagues/1/roster/x", fetched_at=AT, body=BODY, meta={})
    store.put(source="espn", dataset="leagues/1/rosters", fetched_at=AT, body=BODY, meta={})
    keys = store.keys("espn", "leagues/1/roster")
    assert [k.split("/")[4] for k in keys] == ["2026-11-06", "2026-11-07", "2026-11-08"]
    assert store.keys("espn", "nothing") == []


def test_keys_on_one_utc_day() -> None:
    store, _ = fake_store()
    store.put(source="sleeper", dataset="players", fetched_at=AT, body=BODY, meta={})
    assert store.keys_on("sleeper", "players", date(2026, 11, 6)) == [
        "sleeper/players/2026-11-06/113005123456Z"
    ]
    assert store.keys_on("sleeper", "players", date(2026, 11, 7)) == []


def test_matches_the_local_store_listing(tmp_path: Path) -> None:
    store, _ = fake_store()
    local = LocalRawStore(tmp_path)
    for target in (store, local):
        for day in (6, 7):
            at = datetime(2026, 11, day, 11, 30, tzinfo=UTC)
            target.put(source="espn", dataset="season", fetched_at=at, body=BODY, meta={})
    assert store.keys("espn", "season") == local.keys("espn", "season")
    on = date(2026, 11, 7)
    assert store.keys_on("espn", "season", on) == local.keys_on("espn", "season", on)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (client_error("InternalError", 500), RawStoreUnavailableError),
        (client_error("SlowDown", 503), RawStoreUnavailableError),
        (
            EndpointConnectionError(endpoint_url="https://acct-123.r2.example"),
            RawStoreUnavailableError,
        ),
        (client_error("AccessDenied", 403), RawStoreError),
        (client_error("InvalidAccessKeyId", 403), RawStoreError),
    ],
)
def test_errors_say_whether_a_retry_can_help(error: Exception, kind: type[RawStoreError]) -> None:
    store, s3 = fake_store()
    s3.fail_next["put_object"] = error
    with pytest.raises(RawStoreError) as caught:
        store.put(source="espn", dataset="players", fetched_at=AT, body=BODY, meta={})
    assert type(caught.value) is kind
    assert "acct-123" not in str(caught.value)  # the endpoint carries the account ID


def test_reading_a_missing_payload_is_a_store_error() -> None:
    store, _ = fake_store()
    with pytest.raises(RawStoreError, match="no raw object"):
        store.get("espn/players/2026-11-06/113005123456Z")


def test_the_probe_proves_r2_refuses_overwrites() -> None:
    store, s3 = fake_store()
    assert store.probe_never_overwrites() is True
    assert store.probe_never_overwrites() is True  # the probe object persists; still refused
    assert list(s3.objects) == [PROBE_NAME]


def test_the_probe_catches_a_bucket_that_ignores_if_none_match() -> None:
    s3 = FakeS3()
    s3.enforce_if_none_match = False
    store, _ = fake_store(s3)
    assert store.probe_never_overwrites() is False


def test_settings_choose_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_DATA_DIR", str(tmp_path))
    assert isinstance(raw_store_from_settings(Settings()), LocalRawStore)
    monkeypatch.setenv("FH_RAW_STORE", "r2")
    monkeypatch.setenv("FH_R2_ACCESS_KEY_ID", "id")
    with pytest.raises(RawStoreError, match="FH_R2_ENDPOINT, FH_R2_SECRET_ACCESS_KEY"):
        raw_store_from_settings(Settings())


def test_a_configured_r2_store_builds_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("FH_R2_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("FH_R2_SECRET_ACCESS_KEY", "secret")
    assert isinstance(r2_store_from_settings(Settings()), R2RawStore)


def test_an_overwrite_refusal_is_its_own_error() -> None:
    store, _ = fake_store()
    store.put(source="espn", dataset="players", fetched_at=AT, body=b"1", meta={})
    with pytest.raises(RawStoreConflictError):
        store.put(source="espn", dataset="players", fetched_at=AT, body=b"2", meta={})


@pytest.mark.parametrize("code", ["SignatureDoesNotMatch", "InvalidAccessKeyId", "AccessDenied"])
def test_the_probe_never_reads_bad_keys_as_a_pass(code: str) -> None:
    """Found live on 2026-11-06: a mistyped secret key made every write fail with 403, and the
    first probe counted that as R2 refusing an overwrite."""
    store, s3 = fake_store()
    s3.fail_next["put_object"] = client_error(code, 403)
    with pytest.raises(RawStoreError) as caught:
        store.probe_never_overwrites()
    assert not isinstance(caught.value, RawStoreConflictError)
    assert code in str(caught.value)


def test_credential_errors_say_what_to_fix() -> None:
    store, s3 = fake_store()
    s3.fail_next["list_objects_v2"] = client_error("SignatureDoesNotMatch", 403, "ListObjectsV2")
    with pytest.raises(RawStoreError, match="secret access key doesn't match the access key ID"):
        store.check_access()
