"""The raw zone on Cloudflare R2, through its S3-compatible API (ADR 0023).

Same keys, bytes and sidecars as `LocalRawStore`. Nothing is ever overwritten, by two guards:
every write first checks that the object doesn't exist, then sends `If-None-Match: *`, which R2
turns into an atomic refusal (`fh doctor --only raw` proves that against the live bucket).
Uploads carry `Content-MD5`, so R2 rejects a body that was corrupted on the way.

Errors become `RawStoreError`; outages (network, R2's 5xx, throttling) become
`RawStoreUnavailableError`, which the retry layer treats as worth another try. Messages never
include the endpoint, which carries the account ID, or any credential.
"""

import base64
import gzip
import hashlib
import json
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from fantasy_helper.config import Settings
from fantasy_helper.storage.raw_store import (
    META_SUFFIX,
    PAYLOAD_SUFFIX,
    RawStoreConflictError,
    RawStoreError,
    RawStoreUnavailableError,
    is_payload_in_dataset,
    make_key,
    r2_missing_settings,
    sidecar_bytes,
)

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

PROBE_NAME = "_doctor/never-overwrite-probe"
_REFUSED = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})
_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})
_THROTTLED = frozenset({"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout"})
# Plain words for the errors someone has to fix by hand. Codes only, never values.
_HINTS = {
    "SignatureDoesNotMatch": "the secret access key doesn't match the access key ID",
    "InvalidAccessKeyId": "R2 doesn't recognize the access key ID",
    "AccessDenied": "the token isn't allowed to use this bucket",
    "403": "access denied: check the R2 token's keys and bucket scope",
    "NoSuchBucket": "there's no bucket by that name",
}


@dataclass(frozen=True)
class StoredObject:
    name: str
    etag: str  # the MD5 of the object's bytes, for single-part uploads (all of ours)
    size: int


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class R2RawStore:
    def __init__(self, client: "S3Client", bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    # ------------------------------------------------------------------ the RawStore protocol

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
        self.create(
            f"{key}{META_SUFFIX}",
            sidecar_bytes(key, source, dataset, fetched_at, body, meta),
            content_type="application/json",
        )
        self.create(
            f"{key}{PAYLOAD_SUFFIX}",
            gzip.compress(body, mtime=0),
            content_type="application/gzip",
        )
        return key

    def get(self, key: str) -> bytes:
        return gzip.decompress(self.read(f"{key}{PAYLOAD_SUFFIX}"))

    def meta(self, key: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.read(f"{key}{META_SUFFIX}"))
        return data

    def keys(self, source: str, dataset: str) -> list[str]:
        """Every stored payload for a dataset, oldest first."""
        prefix = f"{source}/{dataset}/"
        return sorted(
            obj.name.removesuffix(PAYLOAD_SUFFIX)
            for obj in self.objects(prefix)
            if is_payload_in_dataset(obj.name[len(prefix) :])
        )

    def keys_on(self, source: str, dataset: str, day: date) -> list[str]:
        """The payloads a dataset saved on one UTC day, oldest first."""
        prefix = f"{source}/{dataset}/{day.isoformat()}/"
        return sorted(
            obj.name.removesuffix(PAYLOAD_SUFFIX)
            for obj in self.objects(prefix)
            if "/" not in obj.name[len(prefix) :] and obj.name.endswith(PAYLOAD_SUFFIX)
        )

    # ------------------------------------------------------------------ objects

    def create(
        self, name: str, data: bytes, *, content_type: str, check_first: bool = True
    ) -> None:
        """Write a new object. Raises RawStoreConflictError if it already exists."""
        if check_first and self.exists(name):
            raise RawStoreConflictError(f"refusing to overwrite raw payload {name}")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=name,
                Body=data,
                ContentType=content_type,
                ContentMD5=base64.b64encode(
                    hashlib.md5(data, usedforsecurity=False).digest()
                ).decode(),
                IfNoneMatch="*",
            )
        except (ClientError, BotoCoreError) as exc:
            if _code(exc) in _REFUSED:
                raise RawStoreConflictError(f"refusing to overwrite raw payload {name}") from exc
            raise _translate(exc, "write") from exc

    def read(self, name: str) -> bytes:
        try:
            body: bytes = self._client.get_object(Bucket=self._bucket, Key=name)["Body"].read()
        except (ClientError, BotoCoreError) as exc:
            if _code(exc) in _NOT_FOUND:
                raise RawStoreError(f"no raw object {name}") from exc
            raise _translate(exc, "read") from exc
        return body

    def exists(self, name: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=name)
        except (ClientError, BotoCoreError) as exc:
            if _code(exc) in _NOT_FOUND:
                return False
            raise _translate(exc, "check") from exc
        return True

    def objects(self, prefix: str = "") -> Iterator[StoredObject]:
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=prefix
            )
            for page in pages:
                for obj in page.get("Contents", []):
                    yield StoredObject(
                        name=obj["Key"], etag=obj["ETag"].strip('"'), size=obj["Size"]
                    )
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, "list") from exc

    def create_many(
        self, items: list[tuple[str, bytes, str]], *, workers: int = 8
    ) -> list[tuple[str, RawStoreError | None]]:
        """Create several objects in parallel: (name, error or None) for each, in order."""

        def one(item: tuple[str, bytes, str]) -> tuple[str, RawStoreError | None]:
            name, data, content_type = item
            try:
                self.create(name, data, content_type=content_type)
            except RawStoreError as exc:
                return name, exc
            return name, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, items))

    def check_access(self) -> None:
        """One small listing: proves the endpoint, the keys and the bucket. Raises if not."""
        next(iter(self.objects("_doctor/")), None)

    def probe_never_overwrites(self) -> bool:
        """Write the probe object twice without the existence check. True only if R2 refused a
        write because the object exists, i.e. it enforces `If-None-Match` and can't overwrite
        even in a race. Any other error (bad keys, no access) is raised, never read as a pass."""
        for _ in range(2):
            try:
                self.create(PROBE_NAME, b"probe", content_type="text/plain", check_first=False)
            except RawStoreConflictError:
                return True
        return False


def _code(exc: ClientError | BotoCoreError) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", ""))
    return ""


def _translate(exc: ClientError | BotoCoreError, action: str) -> RawStoreError:
    """A safe message: the error's code or type, never the endpoint URL or a credential."""
    if isinstance(exc, BotoCoreError):  # connection errors and timeouts, after boto's retries
        return RawStoreUnavailableError(f"R2 unreachable ({action}: {type(exc).__name__})")
    code = _code(exc)
    status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    if status >= 500 or code in _THROTTLED:
        return RawStoreUnavailableError(f"R2 unavailable ({action}: {code or status})")
    hint = _HINTS.get(code or str(status))
    return RawStoreError(f"R2 {action} failed: {code or status}" + (f" ({hint})" if hint else ""))


def make_r2_client(endpoint: str, access_key_id: str, secret_access_key: str) -> "S3Client":
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=Config(
            retries={"mode": "standard", "max_attempts": 4},
            connect_timeout=10,
            read_timeout=60,
            # R2 doesn't need botocore's newer default checksums; Content-MD5 covers integrity.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def r2_store_from_settings(settings: Settings) -> R2RawStore:
    endpoint, key_id, secret = (
        settings.r2_endpoint,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
    )
    if endpoint is None or key_id is None or secret is None:
        missing = ", ".join(r2_missing_settings(settings))
        raise RawStoreError(f"the R2 raw store needs {missing} (see .env.example)")
    client = make_r2_client(
        endpoint.get_secret_value(), key_id.get_secret_value(), secret.get_secret_value()
    )
    return R2RawStore(client, settings.r2_bucket)
