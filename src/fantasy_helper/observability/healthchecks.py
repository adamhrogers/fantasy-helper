"""The missed-run watchdog: pings to Healthchecks.io from each scheduled run (ADR 0025).

A run that never starts, or dies without a word, can't alert anyone itself. Each scheduled run
pings `start` when it begins and `success` once it has finished **and reported its own outcome**
(a clean run, or a failure it already sent to the phone). Anything else, such as a crash the app
couldn't report, pings `fail`. Healthchecks alerts the prod ntfy topic when a run is late,
missing, stuck between start and success, or failed.

So a degraded snapshot counts as `success` here: the run's own events already alerted, and a
second, louder alert from the watchdog would only repeat it.

The ping URL holds the project's ping key, so it is never logged, and a failed ping is logged
by its error type only. A ping never fails a run.
"""

import uuid
from typing import Protocol

import httpx

from fantasy_helper.config import Settings
from fantasy_helper.observability.logs import get_logger

log = get_logger(__name__)

MAX_BODY = 1000  # Healthchecks keeps up to 10 KB; a short reason is all we send


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
    ) -> None:
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
        try:
            response = self._client.post(
                self._url + suffix,
                params={"create": "1", "rid": self._rid},
                content=body[:MAX_BODY].encode(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The exception's text would include the URL, and with it the ping key.
            log.warning(
                "healthchecks_ping_failed",
                check=self._slug,
                signal=signal,
                error=type(exc).__name__,
            )


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
        ping_key=settings.healthchecks_ping_key.get_secret_value(),
        slug=slug,
    )
