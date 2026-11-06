import json
import os
from typing import Any

import httpx
import pytest

from fantasy_helper.config import Settings
from fantasy_helper.domain.ports import Alert, AlertPriority
from fantasy_helper.observability.alerts import LogNotifier, NtfyNotifier, notifier_from_settings
from fantasy_helper.observability.logs import REDACTED, redact_secrets
from fantasy_helper.observability.tracing import configure_tracing


def test_secret_fields_are_redacted() -> None:
    event: dict[str, Any] = {
        "event": "ingest",
        "espn_s2": "cookie",
        "SWID": "{id}",
        "langsmith_api_key": "lsv2",
        "access_token": "t",
        "database_url": "postgresql://u:p@h/db",
        "tokens_in": 1200,
        "league": "Points League",
    }
    out = redact_secrets(None, "info", event)
    for key in ("espn_s2", "SWID", "langsmith_api_key", "access_token", "database_url"):
        assert out[key] == REDACTED
    assert out["tokens_in"] == 1200  # LLM token counts are not secrets
    assert out["league"] == "Points League"


def test_ntfy_notifier_publishes_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = NtfyNotifier(server="https://ntfy.example", topic="fh-topic", client=client)
    notifier.send(Alert(title="Run failed", message="daily run", priority=AlertPriority.HIGH))

    assert str(seen[0].url) == "https://ntfy.example/"
    assert json.loads(seen[0].content) == {
        "topic": "fh-topic",
        "title": "Run failed",
        "message": "daily run",
        "priority": 4,
        "tags": [],
    }


def test_ntfy_errors_raise() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    notifier = NtfyNotifier(server="https://ntfy.example", topic="t", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        notifier.send(Alert(title="x", message="y"))


def test_without_a_topic_alerts_go_to_the_log() -> None:
    assert isinstance(notifier_from_settings(Settings()), LogNotifier)


def test_tracing_stays_off_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert configure_tracing(Settings()) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_tracing_turns_on_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert configure_tracing(Settings()) is True
    assert os.environ["LANGSMITH_PROJECT"] == "fantasy-helper"
