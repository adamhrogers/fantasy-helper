"""The raw zone: every source response exactly as received, gzipped, never rewritten.

Layout: <root>/<source>/<dataset>/<YYYY-MM-DD>/<HHMMSSffffff>Z.json.gz, with a .meta.json
sidecar (URL, params, status, hash, size). Dates are UTC. Local disk until M3, then R2
(docs/architecture.md §5.1). A daily snapshot that's missed or overwritten can't be
recovered (ADR 0014), so writes refuse to replace an existing key.
"""

import gzip
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class RawStoreError(Exception):
    pass


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


def make_key(source: str, dataset: str, fetched_at: datetime) -> str:
    if fetched_at.tzinfo is None:
        raise RawStoreError("fetched_at must be timezone-aware")
    utc = fetched_at.astimezone(UTC)
    return f"{source}/{dataset}/{utc:%Y-%m-%d}/{utc:%H%M%S%f}Z"


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
        payload_path = self._root / f"{key}.json.gz"
        if payload_path.exists():
            raise RawStoreError(f"refusing to overwrite raw payload {key}")
        payload_path.parent.mkdir(parents=True, exist_ok=True)
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
        _write_atomic(payload_path, gzip.compress(body, mtime=0))
        _write_atomic(
            self._root / f"{key}.meta.json", json.dumps(sidecar, indent=2, sort_keys=True).encode()
        )
        return key

    def get(self, key: str) -> bytes:
        return gzip.decompress((self._root / f"{key}.json.gz").read_bytes())

    def meta(self, key: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self._root / f"{key}.meta.json").read_text())
        return data

    def keys(self, source: str, dataset: str) -> list[str]:
        """Every stored payload for a dataset, oldest first."""
        base = self._root / source / dataset
        return sorted(
            str(path.relative_to(self._root)).removesuffix(".json.gz")
            for path in base.glob("*/*.json.gz")
        )


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
