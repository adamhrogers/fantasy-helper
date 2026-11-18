"""The missed-run watchdog: pings to Healthchecks.io from each scheduled run (ADR 0025).

A run that never starts, or dies without a word, can't alert anyone itself. Each scheduled run
pings `start` when it begins, `success` when it exits cleanly, and `fail` for any other exit
(`cli._common.watched`). Healthchecks alerts the prod ntfy topic when a run is late, missing,
stuck between start and success, or failed.

A degraded run therefore pings `fail` even though its own events already alerted. That second
alert is deliberate: run events never let a failed send break a run, so "the app reported it"
can mean nobody was told (ntfy down), and the watchdog is the one alert that doesn't depend on it.

A single dropped connection on the closing `success` ping would look like a dead job (2026-11-17:
a false "down" at 17:47 after a snapshot that had saved everything), so a ping that fails to
connect or gets a server error is tried again, a few times, before it is given up on.

The ping URL holds the project's ping key, so it is never logged, and a failed ping is logged
by its error type (and HTTP status, when there was one) only. **A ping never raises**: a broken
watchdog must not cost a snapshot.
"""

import time
import uuid
from collections.abc import Callable
from typing import Protocol

import httpx

from fantasy_helper.config import Settings
from fantasy_helper.observability.logs import get_logger

log = get_logger(__name__)

MAX_BODY = 1000  # Healthchecks keeps up to 10 KB; a short reason is all we send
RETRY_DELAYS = (1.0, 4.0)  # seconds before the second and third tries: three tries in all


def _retryable(exc: Exception) -> bool:
    """Connection trouble and server errors may pass; a bad key or a 4xx never will."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError)


class Heartbeat(Protocol):
    def start(self) -> None: ...
    def success(self, detail: str = "") -> None: ...
    def fail(self, detail: str = "") -> None: ...


class HealthchecksHeartbeat:
    """Pings one check, identified by slug, creating it on the first ping (`create=1`)."""

    def __init__(
        self,
        *,
        server: str,
        ping_key: str,
        slug: str,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._url = f"{server.rstrip('/')}/{ping_key}/{slug}"
        self._slug = slug
        # One run id pairs this run's start with its end, so Healthchecks shows its duration.
        self._rid = str(uuid.uuid4())
        self._client = client or httpx.Client(timeout=timeout)

    def start(self) -> None:
        self._ping("/start", "")

    def success(self, detail: str = "") -> None:
        self._ping("", detail)

    def fail(self, detail: str = "") -> None:
        self._ping("/fail", detail)

    def _ping(self, suffix: str, body: str) -> None:
        signal = suffix.lstrip("/") or "success"
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = self._client.post(
                    self._url + suffix,
                    params={"create": "1", "rid": self._rid},
                    content=body[:MAX_BODY].encode(),
                )
                response.raise_for_status()
                return
            except Exception as exc:  # not just httpx.HTTPError: a bad key raises InvalidURL
                if attempt < len(RETRY_DELAYS) and _retryable(exc):
                    self._sleep(RETRY_DELAYS[attempt])
                    continue
                # The exception's text would include the URL, and with it the ping key.
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                log.warning(
                    "healthchecks_ping_failed",
                    check=self._slug,
                    signal=signal,
                    error=type(exc).__name__,
                    status=status,
                    attempts=attempt + 1,
                )
                return


class NoHeartbeat:
    """No ping key (the laptop, CI, runs started by hand): nothing to watch."""

    def start(self) -> None:
        pass

    def success(self, detail: str = "") -> None:
        pass

    def fail(self, detail: str = "") -> None:
        pass


def heartbeat_from_settings(settings: Settings, slug: str) -> Heartbeat:
    if settings.healthchecks_ping_key is None:
        return NoHeartbeat()
    return HealthchecksHeartbeat(
        server=settings.healthchecks_server,
        # A value pasted with a trailing newline would otherwise land in the URL path.
        ping_key=settings.healthchecks_ping_key.get_secret_value().strip(),
        slug=slug,
    )
