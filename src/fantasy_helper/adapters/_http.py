"""Shared HTTP plumbing for adapters: one client setup, retries, and raw responses.

Adapters return `RawResponse` with the exact bytes received, so the pipeline can store them
untouched (daily snapshots, recorded fixtures) before anything parses them.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx

from fantasy_helper.domain.ports import Clock, SystemClock

USER_AGENT = "fantasy-helper/0.1 (personal project)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# A list value repeats the key: {"view": ["mSettings", "mTeam"]} -> view=mSettings&view=mTeam
QueryParams = Mapping[str, str | list[str]]


def _sleep(seconds: float) -> None:
    """Retry backoff. A module function so tests can replace it."""
    time.sleep(seconds)


@dataclass(frozen=True)
class RawResponse:
    source: str  # "espn" | "sleeper"
    dataset: str  # e.g. "players_projections"
    url: str
    params: QueryParams
    # Request headers worth recording (filters). Never cookies or auth headers.
    headers: Mapping[str, str]
    status_code: int
    body: bytes
    fetched_at: datetime
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status_code == 200


def make_client(
    *, timeout: float = 60.0, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        transport=transport,
    )


def get_raw(
    client: httpx.Client,
    *,
    source: str,
    dataset: str,
    url: str,
    params: QueryParams | None = None,
    headers: Mapping[str, str] | None = None,
    secret_headers: Mapping[str, str] | None = None,
    attempts: int = 3,
    backoff_seconds: float = 2.0,
    clock: Clock | None = None,
    sleep: Callable[[float], None] | None = None,
) -> RawResponse:
    """GET with retries on connection errors, 429 and 5xx. Returns the last response, ok or not.

    `secret_headers` (cookies, auth) are sent but never recorded in the RawResponse, so they
    can't reach stored metadata, logs or fixtures.

    Raises httpx.TransportError only if every attempt failed to connect.
    """
    clock = clock or SystemClock()
    params = dict(params or {})
    headers = dict(headers or {})
    for attempt in range(1, attempts + 1):
        try:
            # httpx drops a URL's own query string whenever `params` is passed, even empty.
            response = client.get(
                url, params=params or None, headers={**headers, **(secret_headers or {})}
            )
        except httpx.TransportError:
            if attempt == attempts:
                raise
        else:
            if response.status_code not in RETRY_STATUSES or attempt == attempts:
                return RawResponse(
                    source=source,
                    dataset=dataset,
                    url=url,
                    params=params,
                    headers=headers,
                    status_code=response.status_code,
                    body=response.content,
                    fetched_at=clock.now(),
                    attempts=attempt,
                )
        (sleep or _sleep)(backoff_seconds * attempt)
    raise AssertionError("unreachable")  # pragma: no cover
