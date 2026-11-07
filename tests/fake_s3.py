"""An in-memory stand-in for the slice of the S3 API that `R2RawStore` uses.

It behaves like R2 where our guarantees depend on it: `If-None-Match: *` refuses an existing
key with 412, a wrong `Content-MD5` is rejected, listings come back in pages, and a listing
with no matches has no `Contents`. `fh doctor --only raw` checks the first of these against the
live bucket.
"""

import base64
import hashlib
import io
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

from botocore.exceptions import ClientError

from fantasy_helper.storage.r2_store import R2RawStore

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client


def client_error(code: str, status: int, operation: str = "PutObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},  # type: ignore[typeddict-item]
        operation,
    )


class FakeS3:
    def __init__(self, page_size: int = 2) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.page_size = page_size
        self.enforce_if_none_match = True
        self.hidden_from_head: set[str] = set()  # HEAD says absent: simulates a racing writer
        self.fail_next: dict[str, Exception] = {}  # operation -> error, raised once
        self.puts: list[str] = []

    def _maybe_fail(self, operation: str) -> None:
        error = self.fail_next.pop(operation, None)
        if error is not None:
            raise error

    def put_object(self, **kw: Any) -> dict[str, Any]:
        self._maybe_fail("put_object")
        key, body = kw["Key"], kw["Body"]
        if kw.get("ContentMD5") != base64.b64encode(hashlib.md5(body).digest()).decode():  # noqa: S324
            raise client_error("BadDigest", 400)
        if kw.get("IfNoneMatch") == "*" and self.enforce_if_none_match and key in self.objects:
            raise client_error("PreconditionFailed", 412)
        self.objects[key] = body
        self.content_types[key] = kw["ContentType"]
        self.puts.append(key)
        return {}

    def get_object(self, **kw: Any) -> dict[str, Any]:
        self._maybe_fail("get_object")
        if kw["Key"] not in self.objects:
            raise client_error("NoSuchKey", 404, "GetObject")
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}

    def head_object(self, **kw: Any) -> dict[str, Any]:
        self._maybe_fail("head_object")
        if kw["Key"] not in self.objects or kw["Key"] in self.hidden_from_head:
            raise client_error("404", 404, "HeadObject")
        return {}

    def get_paginator(self, name: str) -> "FakePaginator":
        assert name == "list_objects_v2"
        return FakePaginator(self)


class FakePaginator:
    def __init__(self, s3: FakeS3) -> None:
        self._s3 = s3

    def paginate(self, **kw: Any) -> Iterator[dict[str, Any]]:
        self._s3._maybe_fail("list_objects_v2")
        names = sorted(k for k in self._s3.objects if k.startswith(kw.get("Prefix", "")))
        if not names:
            yield {"KeyCount": 0}
        for start in range(0, len(names), self._s3.page_size):
            yield {
                "Contents": [
                    {
                        "Key": name,
                        "ETag": f'"{hashlib.md5(self._s3.objects[name]).hexdigest()}"',  # noqa: S324
                        "Size": len(self._s3.objects[name]),
                    }
                    for name in names[start : start + self._s3.page_size]
                ]
            }


def fake_store(s3: FakeS3 | None = None) -> tuple[R2RawStore, FakeS3]:
    s3 = s3 or FakeS3()
    return R2RawStore(cast("S3Client", s3), "fantasy-helper-raw"), s3
