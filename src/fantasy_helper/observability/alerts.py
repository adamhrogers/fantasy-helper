"""Ops alerts: tell the operator when something breaks.

These go through ntfy, separately from product push, so a broken phone app can't hide its own
outage (docs/architecture.md §9). Alert text must never contain secrets or league data.
"""

import httpx

from fantasy_helper.config import Settings
from fantasy_helper.domain.ports import Alert, Notifier
from fantasy_helper.observability.logs import get_logger

log = get_logger(__name__)


class NtfyNotifier:
    """Publishes alerts to a private ntfy topic using ntfy's JSON publish API."""

    def __init__(
        self, *, server: str, topic: str, client: httpx.Client | None = None, timeout: float = 10.0
    ) -> None:
        self._url = server.rstrip("/") + "/"
        self._topic = topic
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, alert: Alert) -> None:
        response = self._client.post(
            self._url,
            json={
                "topic": self._topic,
                "title": alert.title,
                "message": alert.message,
                "priority": int(alert.priority),
                "tags": list(alert.tags),
            },
        )
        response.raise_for_status()


class LogNotifier:
    """Fallback when no ntfy topic is configured: the alert goes to the log instead."""

    def send(self, alert: Alert) -> None:
        log.warning("ops_alert", title=alert.title, message=alert.message, priority=alert.priority)


def notifier_from_settings(settings: Settings) -> Notifier:
    if settings.ntfy_topic is None:
        return LogNotifier()
    return NtfyNotifier(server=settings.ntfy_server, topic=settings.ntfy_topic.get_secret_value())
