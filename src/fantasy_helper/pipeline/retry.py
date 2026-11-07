"""Run-level retries: decide whether a failed part of a run is worth trying again.

Three layers, from fastest to slowest (docs/runbooks/run-events.md, "Retries"):

1. **Each request** (`adapters._http.get_raw`): 3 attempts within about 6 seconds, for connection
   errors, 429 and 5xx. That covers blips.
2. **Each part of a run** (this module): a dataset or league that still failed is classified.
   If retrying can help (`FailureKind.retryable`), only the failed parts run again, after 2 and
   then 5 more minutes: 3 attempts in all. That covers outages of a few minutes, and keeps a
   06:30 run well clear of the 8 a.m. report. Past that, waiting longer rarely helps.
3. **A run that never starts** (the Mac is off, a scheduler skipped it) can't retry itself: the
   missed-run watchdog (M3) catches it.

The classifier is plain rules, not a model: the cause decides. Expired cookies, a league that's
gone, a changed data format or a bug fail at once, because rerunning can't change the outcome.
"""

import json
import time
from collections.abc import Callable

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from fantasy_helper.adapters.espn.mapper import EspnMappingError
from fantasy_helper.adapters.sleeper.mapper import SleeperMappingError
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.storage.raw_store import RawStoreError, RawStoreUnavailableError

MAX_ATTEMPTS = 3
RETRY_WAITS_SECONDS = (120.0, 300.0)  # before attempt 2, before attempt 3


def classify_status(status_code: int) -> FailureKind:
    """An HTTP status that's still wrong after the request-level retries."""
    if status_code == 429:
        return FailureKind.RATE_LIMITED
    if status_code >= 500:
        return FailureKind.SERVER_ERROR
    if status_code in (401, 403):
        return FailureKind.AUTH
    if status_code in (404, 410):
        return FailureKind.NOT_FOUND
    return FailureKind.BAD_RESPONSE


def classify_exception(exc: BaseException) -> FailureKind:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return classify_status(status)
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_status(exc.response.status_code)
    if isinstance(exc, httpx.TransportError):  # connection refused, DNS, timeouts
        return FailureKind.NETWORK
    if isinstance(exc, OperationalError):
        return FailureKind.DATABASE
    if isinstance(exc, (EspnMappingError, SleeperMappingError, ValidationError)):
        return FailureKind.FORMAT_CHANGED
    if isinstance(exc, json.JSONDecodeError):
        return FailureKind.BAD_RESPONSE
    if isinstance(exc, RawStoreUnavailableError):
        return FailureKind.STORAGE_DOWN
    if isinstance(exc, (RawStoreError, OSError)):
        return FailureKind.STORAGE
    return FailureKind.BUG


def wait_before(attempt: int, waits: tuple[float, ...] = RETRY_WAITS_SECONDS) -> float:
    """Seconds to wait before `attempt` (2, 3, …)."""
    return waits[min(attempt - 2, len(waits) - 1)]


def outcome(kind: FailureKind, attempts: int) -> str:
    """How a part ended, for the phone: '…, after 3 attempts' or '…, not retried'."""
    if kind.retryable:
        return f"{kind.describe}, after {attempts} attempt{'s' if attempts != 1 else ''}"
    return f"{kind.describe}; not retried (retrying can't fix it)"


class RunStoppedError(Exception):
    """A run gave up: a cause retrying can't fix, or attempts ran out. The original exception
    is the __cause__; `kind` and `attempts` are what the phone hears."""

    def __init__(self, kind: FailureKind, attempts: int) -> None:
        super().__init__(f"{kind.value} after {attempts} attempt(s)")
        self.kind = kind
        self.attempts = attempts


def run_with_retries[T](
    once: Callable[[], T],
    events: RunEvents,
    *,
    retryable_parts: Callable[[T], int] = lambda _result: 0,
    describe_parts: Callable[[T, int], tuple[str, list[str]]] | None = None,
    passthrough: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
    waits: tuple[float, ...] = RETRY_WAITS_SECONDS,
) -> tuple[T, int]:
    """Run an idempotent job, retrying when that can help. Returns (result, attempts).

    - A crash is classified: retried if its cause can clear up, else `RunStoppedError` at once.
    - A result with retryable failed parts (`retryable_parts` > 0) runs again; the job must be
      safe to rerun (what worked stays as it is).
    - Exceptions in `passthrough` (the caller's own) are raised untouched.
    """
    attempt = 1
    while True:
        try:
            result = once()
        except passthrough:
            raise
        except Exception as exc:
            kind = classify_exception(exc)
            if not kind.retryable or attempt >= MAX_ATTEMPTS:
                raise RunStoppedError(kind, attempt) from exc
            attempt += 1
            wait = wait_before(attempt, waits)
            events.retrying(
                attempt, MAX_ATTEMPTS, wait, f"Stopped: {kind.describe}. Nothing was saved."
            )
            sleep(wait)
            continue
        if not retryable_parts(result) or attempt >= MAX_ATTEMPTS:
            return result, attempt
        attempt += 1
        wait = wait_before(attempt, waits)
        detail, problems = (
            describe_parts(result, attempt - 1) if describe_parts else ("Some parts failed.", [])
        )
        events.retrying(attempt, MAX_ATTEMPTS, wait, detail, problems)
        sleep(wait)
