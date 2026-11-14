import httpx
import pytest
from structlog.testing import capture_logs

from fantasy_helper.config import Settings
from fantasy_helper.observability.healthchecks import (
    MAX_BODY,
    HealthchecksHeartbeat,
    NoHeartbeat,
    heartbeat_from_settings,
)

KEY = "ping-key-very-secret"


def _heartbeat(handler: httpx.MockTransport) -> HealthchecksHeartbeat:
    client = httpx.Client(transport=handler)
    return HealthchecksHeartbeat(
        server="https://hc.example/", ping_key=KEY, slug="snapshot-morning", client=client
    )


def test_pings_start_success_and_fail_on_one_run_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="OK")

    beat = _heartbeat(httpx.MockTransport(handler))
    beat.start()
    beat.success("done")
    beat.fail("x" * (MAX_BODY + 50))

    paths = [r.url.path for r in seen]
    assert paths == [
        f"/{KEY}/snapshot-morning/start",
        f"/{KEY}/snapshot-morning",
        f"/{KEY}/snapshot-morning/fail",
    ]
    assert {r.url.params["create"] for r in seen} == {"1"}
    assert len({r.url.params["rid"] for r in seen}) == 1
    assert seen[1].content == b"done"
    assert len(seen[2].content) == MAX_BODY


@pytest.mark.parametrize(
    "transport",
    [
        httpx.MockTransport(lambda _r: httpx.Response(500)),
        httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r))),
    ],
)
def test_a_failed_ping_never_fails_the_run_or_logs_the_key(transport: httpx.MockTransport) -> None:
    with capture_logs() as logs:
        _heartbeat(transport).success()
    assert [e["event"] for e in logs] == ["healthchecks_ping_failed"]
    assert KEY not in repr(logs)


def test_no_ping_key_means_no_pings() -> None:
    assert isinstance(heartbeat_from_settings(Settings(), "snapshot-morning"), NoHeartbeat)


def test_a_ping_key_turns_the_watchdog_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_HEALTHCHECKS_PING_KEY", KEY)
    settings = Settings()
    assert isinstance(heartbeat_from_settings(settings, "snapshot-evening"), HealthchecksHeartbeat)
    assert KEY not in repr(settings)


def test_a_key_pasted_with_a_newline_still_pings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_HEALTHCHECKS_PING_KEY", f"{KEY}\n")
    beat = heartbeat_from_settings(Settings(), "snapshot-morning")
    assert isinstance(beat, HealthchecksHeartbeat)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    beat._client = httpx.Client(transport=httpx.MockTransport(handler))
    beat.start()
    assert seen[0].url.path == f"/{KEY}/snapshot-morning/start"


def test_a_key_that_cant_form_a_url_is_logged_not_raised() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    beat = HealthchecksHeartbeat(
        server="https://hc.example", ping_key="bad\x00key", slug="snapshot-morning", client=client
    )
    with capture_logs() as logs:
        beat.start()
        beat.fail("x")
    assert [e["error"] for e in logs] == ["InvalidURL", "InvalidURL"]
    assert "bad" not in repr(logs)
