from datetime import date

import httpx
import pytest

from fantasy_helper.adapters import _http
from fantasy_helper.adapters.espn import public as espn
from fantasy_helper.adapters.sleeper import public as sleeper
from fantasy_helper.domain.calendar import season_end_year, season_start_year


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_sleep", lambda _seconds: None)


def client_with(responses: list[httpx.Response | Exception]) -> tuple[httpx.Client, list[int]]:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        answer = responses[min(len(calls), len(responses)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return _http.make_client(transport=httpx.MockTransport(handler)), calls


def test_retries_server_errors_then_succeeds() -> None:
    client, calls = client_with([httpx.Response(503), httpx.Response(200, json={})])
    raw = _http.get_raw(client, source="s", dataset="d", url="https://example.test/x")
    assert raw.ok
    assert raw.attempts == 2
    assert len(calls) == 2


def test_gives_up_and_returns_the_last_response() -> None:
    client, calls = client_with([httpx.Response(503)])
    raw = _http.get_raw(client, source="s", dataset="d", url="https://example.test/x", attempts=3)
    assert raw.status_code == 503
    assert len(calls) == 3


def test_client_errors_are_not_retried() -> None:
    client, calls = client_with([httpx.Response(404)])
    assert (
        _http.get_raw(client, source="s", dataset="d", url="https://example.test/x").status_code
        == 404
    )
    assert len(calls) == 1


def test_connection_errors_raise_after_the_last_attempt() -> None:
    client, calls = client_with([httpx.ConnectError("offline")])
    with pytest.raises(httpx.ConnectError):
        _http.get_raw(client, source="s", dataset="d", url="https://example.test/x")
    assert len(calls) == 3


def test_requests_identify_the_app() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with _http.make_client(transport=transport) as client:
        espn.players_projections(client, 2027, limit=5)
    assert seen[0].headers["User-Agent"] == _http.USER_AGENT
    assert '"limit":5' in seen[0].headers["x-fantasy-filter"]
    assert "cookie" not in {k.lower() for k in seen[0].headers}


@pytest.mark.parametrize(
    ("on", "end", "start"),
    [
        (date(2026, 11, 6), 2027, 2026),
        (date(2027, 3, 15), 2027, 2026),
        (date(2027, 9, 1), 2028, 2027),
    ],
)
def test_season_names(on: date, end: int, start: int) -> None:
    assert season_end_year(on) == end
    assert season_start_year(on) == start


def test_validators_catch_shrunken_payloads() -> None:
    assert espn.validate_players({"players": []}) is not None
    assert sleeper.validate_projections([{"stats": {}}] * 500) is not None
    assert sleeper.validate_state({"season": "2026", "week": 3}) is None


def test_espn_validator_expects_a_projected_core_not_a_fraction() -> None:
    def players(projected: int, total: int) -> dict[str, object]:
        rows = [{"player": {"stats": [{"statSourceId": 1}]}} for _ in range(projected)]
        rows += [{"player": {"stats": []}} for _ in range(total - projected)]
        return {"players": rows}

    assert espn.validate_players(players(349, 1100)) is None  # the real 2026-11-06 shape
    assert "only 100 players have projections" in (espn.validate_players(players(100, 1100)) or "")
