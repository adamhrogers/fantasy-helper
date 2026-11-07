"""`fh raw upload`: the laptop backlog goes to R2 byte for byte, and nothing there is replaced."""

from datetime import UTC, datetime
from pathlib import Path

from fantasy_helper.pipeline.raw_upload import upload_backlog
from fantasy_helper.storage.raw_store import LocalRawStore
from tests.fake_s3 import FakeS3, client_error, fake_store


def backlog(root: Path, days: int = 3) -> LocalRawStore:
    store = LocalRawStore(root)
    for day in range(1, days + 1):
        at = datetime(2026, 11, day, 11, 30, tzinfo=UTC)
        store.put(source="espn", dataset="season", fetched_at=at, body=b"{}", meta={"d": day})
        store.put(source="sleeper", dataset="leagues/9/rosters", fetched_at=at, body=b"[]", meta={})
    return store


def test_uploads_every_file_byte_for_byte(tmp_path: Path) -> None:
    local = backlog(tmp_path)
    r2, s3 = fake_store()
    report = upload_backlog(tmp_path, r2)
    assert report.ok
    assert (report.local_files, report.uploaded, report.already_there) == (12, 12, 0)
    for path in tmp_path.rglob("*.json*"):
        assert s3.objects[path.relative_to(tmp_path).as_posix()] == path.read_bytes()
    assert r2.keys("espn", "season") == local.keys("espn", "season")
    key = local.keys("espn", "season")[0]
    assert r2.get(key) == local.get(key)
    assert r2.meta(key) == local.meta(key)


def test_each_sidecar_goes_up_before_its_payload(tmp_path: Path) -> None:
    backlog(tmp_path, days=1)
    r2, s3 = fake_store()
    upload_backlog(tmp_path, r2)
    for name in s3.puts:
        if name.endswith(".json.gz"):
            sidecar = name.removesuffix(".json.gz") + ".meta.json"
            assert s3.puts.index(sidecar) < s3.puts.index(name)


def test_running_it_again_changes_nothing(tmp_path: Path) -> None:
    backlog(tmp_path)
    r2, s3 = fake_store()
    upload_backlog(tmp_path, r2)
    puts = len(s3.puts)
    again = upload_backlog(tmp_path, r2)
    assert again.ok
    assert (again.uploaded, again.already_there) == (0, 12)
    assert len(s3.puts) == puts


def test_different_bytes_in_r2_are_a_conflict_and_left_alone(tmp_path: Path) -> None:
    local = backlog(tmp_path, days=1)
    r2, s3 = fake_store()
    key = local.keys("espn", "season")[0]
    s3.objects[f"{key}.json.gz"] = b"something else"
    report = upload_backlog(tmp_path, r2)
    assert not report.ok
    assert report.conflicts == [f"{key}.json.gz"]
    assert s3.objects[f"{key}.json.gz"] == b"something else"


def test_a_payload_without_a_sidecar_is_reported_not_uploaded(tmp_path: Path) -> None:
    local = backlog(tmp_path, days=1)
    key = local.keys("espn", "season")[0]
    (tmp_path / f"{key}.meta.json").unlink()
    r2, s3 = fake_store()
    report = upload_backlog(tmp_path, r2)
    assert report.orphans == [f"{key}.json.gz"]
    assert f"{key}.json.gz" not in s3.objects
    assert report.uploaded == 2  # the other dataset's pair


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    backlog(tmp_path)
    r2, s3 = fake_store()
    report = upload_backlog(tmp_path, r2, dry_run=True)
    assert (report.to_upload, report.uploaded) == (12, 0)
    assert s3.objects == {}


def test_a_failed_sidecar_holds_back_its_payload(tmp_path: Path) -> None:
    backlog(tmp_path, days=1)
    s3 = FakeS3()
    s3.fail_next["put_object"] = client_error("AccessDenied", 403)
    r2, _ = fake_store(s3)
    report = upload_backlog(tmp_path, r2, workers=1)
    assert not report.ok
    assert len(report.failures) == 2  # the sidecar, and the payload held back with it
    payloads = [n for n in s3.objects if n.endswith(".json.gz")]
    for name in payloads:
        assert name.removesuffix(".json.gz") + ".meta.json" in s3.objects
