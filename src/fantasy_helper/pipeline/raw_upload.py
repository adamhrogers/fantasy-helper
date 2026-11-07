"""`fh raw upload`: copy a local raw backlog to R2, byte for byte (ADR 0023).

Safe to run any number of times. An object already in R2 with the same bytes (MD5) is skipped;
one with different bytes is a conflict, reported and never overwritten. Sidecars go up before
their payloads, so R2 never holds a payload without its sidecar. A local payload that has no
sidecar is reported and left alone. At the end every uploaded object is checked against a fresh
listing of the bucket.
"""

from dataclasses import dataclass, field
from pathlib import Path

from fantasy_helper.observability.logs import get_logger
from fantasy_helper.storage.r2_store import R2RawStore, md5_hex
from fantasy_helper.storage.raw_store import META_SUFFIX, PAYLOAD_SUFFIX

log = get_logger(__name__)

_CONTENT_TYPES = {META_SUFFIX: "application/json", PAYLOAD_SUFFIX: "application/gzip"}


@dataclass
class UploadReport:
    dry_run: bool
    local_files: int = 0
    uploaded: int = 0
    to_upload: int = 0
    already_there: int = 0
    conflicts: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.conflicts or self.orphans or self.failures or self.unverified)


def _kind(name: str) -> str | None:
    for suffix in (META_SUFFIX, PAYLOAD_SUFFIX):
        if name.endswith(suffix):
            return suffix
    return None


def upload_backlog(
    root: Path, store: R2RawStore, *, dry_run: bool = False, workers: int = 8
) -> UploadReport:
    local = {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file() and _kind(path.name) is not None
    }
    report = UploadReport(dry_run=dry_run, local_files=len(local))
    report.orphans = [
        name
        for name in local
        if name.endswith(PAYLOAD_SUFFIX)
        and f"{name.removesuffix(PAYLOAD_SUFFIX)}{META_SUFFIX}" not in local
    ]
    existing = {obj.name: obj.etag for obj in store.objects()}

    todo: dict[str, list[tuple[str, bytes, str]]] = {META_SUFFIX: [], PAYLOAD_SUFFIX: []}
    expected: dict[str, str] = {}
    for name, path in local.items():
        if name in report.orphans:
            continue
        data = path.read_bytes()
        digest = md5_hex(data)
        if name in existing:
            if existing[name] == digest:
                report.already_there += 1
            else:
                report.conflicts.append(name)
            continue
        suffix = META_SUFFIX if name.endswith(META_SUFFIX) else PAYLOAD_SUFFIX
        todo[suffix].append((name, data, _CONTENT_TYPES[suffix]))
        expected[name] = digest

    report.to_upload = len(expected)
    if dry_run or not expected:
        return report

    # Sidecars first. A payload goes up only once its sidecar is in R2.
    written = _create(store, todo[META_SUFFIX], report, workers)
    blocked = {
        name.removesuffix(META_SUFFIX)
        for name in [*report.conflicts, *(n for n, _, _ in todo[META_SUFFIX] if n not in written)]
        if name.endswith(META_SUFFIX)
    }
    payloads = []
    for item in todo[PAYLOAD_SUFFIX]:
        if item[0].removesuffix(PAYLOAD_SUFFIX) in blocked:
            report.failures.append(f"{item[0]}: skipped, because its sidecar isn't in R2")
        else:
            payloads.append(item)
    written |= _create(store, payloads, report, workers)

    report.uploaded = len(written)
    after = {obj.name: obj.etag for obj in store.objects()}
    report.unverified = sorted(name for name in written if after.get(name) != expected[name])
    log.info(
        "raw_upload_finished",
        uploaded=report.uploaded,
        already_there=report.already_there,
        conflicts=len(report.conflicts),
        failures=len(report.failures),
    )
    return report


def _create(
    store: R2RawStore, items: list[tuple[str, bytes, str]], report: UploadReport, workers: int
) -> set[str]:
    """Upload the items; return the names written and add the failures to the report."""
    written: set[str] = set()
    for name, error in store.create_many(items, workers=workers):
        if error is None:
            written.add(name)
        else:
            report.failures.append(f"{name}: {error}")
    return written
