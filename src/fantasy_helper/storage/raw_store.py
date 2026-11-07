"""The raw zone: every source response exactly as received, gzipped, never rewritten.

Layout: <root>/<source>/<dataset>/<YYYY-MM-DD>/<HHMMSSffffff>Z.json.gz, with a .meta.json
sidecar (URL, params, status, hash, size). Dates are UTC. The same layout lives on local disk
(`LocalRawStore`) and in Cloudflare R2 (`storage.r2_store.R2RawStore`, ADR 0023), so a local
backlog uploads byte for byte. A daily snapshot that's missed or overwritten can't be recovered
(ADR 0014), so writes refuse to replace an existing key.

The sidecar is written before the payload: a reader that lists payloads can always read their
sidecars, even while another machine is writing.
"""

import gzip
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from fantasy_helper.config import Settings

PAYLOAD_SUFFIX = ".json.gz"
META_SUFFIX = ".meta.json"


class RawStoreError(Exception):
    pass


class RawStoreUnavailableError(RawStoreError):
    """The store couldn't be reached (network, R2 outage). Trying again later can help."""


class RawStoreConflictError(RawStoreError):
    """The key already exists, and raw payloads are never overwritten."""


class RawStore(Protocol):
    def put(
        self,
        *,
        source: str,
        dataset: str,
        fetched_at: datetime,
        body: bytes,
        meta: Mapping[str, Any],
    ) -> str: ...

    def get(self, key: str) -> bytes: ...

    def meta(self, key: str) -> dict[str, Any]: ...

    def keys(self, source: str, dataset: str) -> list[str]: ...

    def keys_on(self, source: str, dataset: str, day: date) -> list[str]: ...


def make_key(source: str, dataset: str, fetched_at: datetime) -> str:
    if fetched_at.tzinfo is None:
        raise RawStoreError("fetched_at must be timezone-aware")
    utc = fetched_at.astimezone(UTC)
    return f"{source}/{dataset}/{utc:%Y-%m-%d}/{utc:%H%M%S%f}Z"


def fetched_at_from_key(key: str) -> datetime:
    """The UTC time a payload was fetched, read from its key without fetching the sidecar."""
    day, time = key.rsplit("/", 2)[-2:]
    return datetime.strptime(f"{day} {time}", "%Y-%m-%d %H%M%S%fZ").replace(tzinfo=UTC)


def sidecar_bytes(
    key: str, source: str, dataset: str, fetched_at: datetime, body: bytes, meta: Mapping[str, Any]
) -> bytes:
    sidecar = {
        **meta,
        "key": key,
        "source": source,
        "dataset": dataset,
        "fetched_at": fetched_at.astimezone(UTC).isoformat(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "content_encoding": "gzip",
    }
    return json.dumps(sidecar, indent=2, sort_keys=True).encode()


def is_payload_in_dataset(rest: str) -> bool:
    """True for `<YYYY-MM-DD>/<time>Z.json.gz`: one payload directly under a dataset's dates,
    not a payload of a deeper dataset (league datasets nest: `leagues/<id>/<name>`)."""
    parts = rest.split("/")
    return len(parts) == 2 and len(parts[0]) == 10 and parts[1].endswith(PAYLOAD_SUFFIX)


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def put(
        self,
        *,
        source: str,
        dataset: str,
        fetched_at: datetime,
        body: bytes,
        meta: Mapping[str, Any],
    ) -> str:
        key = make_key(source, dataset, fetched_at)
        payload_path = self._root / f"{key}{PAYLOAD_SUFFIX}"
        meta_path = self._root / f"{key}{META_SUFFIX}"
        if payload_path.exists() or meta_path.exists():
            raise RawStoreConflictError(f"refusing to overwrite raw payload {key}")
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(meta_path, sidecar_bytes(key, source, dataset, fetched_at, body, meta))
        _write_atomic(payload_path, gzip.compress(body, mtime=0))
        return key

    def get(self, key: str) -> bytes:
        return gzip.decompress((self._root / f"{key}{PAYLOAD_SUFFIX}").read_bytes())

    def meta(self, key: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self._root / f"{key}{META_SUFFIX}").read_text())
        return data

    def keys(self, source: str, dataset: str) -> list[str]:
        """Every stored payload for a dataset, oldest first."""
        base = self._root / source / dataset
        return sorted(
            str(path.relative_to(self._root)).removesuffix(PAYLOAD_SUFFIX)
            for path in base.glob(f"*/*{PAYLOAD_SUFFIX}")
        )

    def keys_on(self, source: str, dataset: str, day: date) -> list[str]:
        """The payloads a dataset saved on one UTC day, oldest first."""
        base = self._root / source / dataset / day.isoformat()
        return sorted(
            str(path.relative_to(self._root)).removesuffix(PAYLOAD_SUFFIX)
            for path in base.glob(f"*{PAYLOAD_SUFFIX}")
        )


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def r2_missing_settings(settings: Settings) -> list[str]:
    return [
        name
        for name, value in (
            ("FH_R2_ENDPOINT", settings.r2_endpoint),
            ("FH_R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("FH_R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
        )
        if value is None
    ]


def raw_store_from_settings(settings: Settings) -> RawStore:
    """The store FH_RAW_STORE names. Raises RawStoreError if R2 is chosen but not configured."""
    if settings.raw_store == "local":
        return LocalRawStore(settings.data_dir / "raw")
    from fantasy_helper.storage.r2_store import r2_store_from_settings  # boto3 loads only here

    return r2_store_from_settings(settings)
