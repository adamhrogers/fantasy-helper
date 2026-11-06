import httpx
import pytest
from pydantic import SecretStr

from fantasy_helper.adapters import _http
from fantasy_helper.adapters.espn import league as espn_league
from fantasy_helper.adapters.sleeper import league as sleeper_league
from fantasy_helper.domain.league import ScoringType
from fantasy_helper.domain.users import EspnCredentials

CREDS = EspnCredentials(espn_s2=SecretStr("s2-secret-value"), swid=SecretStr("{ABC-123}"))

ESPN_LEAGUE = {
    "id": 555,
    "settings": {
        "name": "Points League",
        "size": 10,
        "scoringSettings": {"scoringType": "H2H_POINTS"},
    },
    "teams": [
        {"id": 1, "name": "Someone Else", "owners": ["{ZZZ-999}"]},
        {"id": 7, "location": "Adam's", "nickname": "Team", "owners": ["{abc-123}"]},
    ],
}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_sleep", lambda _seconds: None)


def mock_client(status: int, payload: object, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    return _http.make_client(transport=httpx.MockTransport(handler))


def test_espn_summary_finds_my_team_by_swid() -> None:
    summary = espn_league.summarize(ESPN_LEAGUE, 2027, "{ABC-123}")
    assert summary.name == "Points League"
    assert summary.team_count == 10
    assert summary.scoring_type is ScoringType.POINTS
    assert summary.my_team_id == "7"
    assert summary.my_team_name == "Adam's Team"


def test_espn_cookies_are_sent_but_never_recorded() -> None:
    seen: list[httpx.Request] = []
    raw = espn_league.fetch_league(mock_client(200, ESPN_LEAGUE, seen), 2027, "555", CREDS)
    assert "espn_s2=s2-secret-value" in seen[0].headers["cookie"]
    assert "view=mSettings" in str(seen[0].url)
    recorded = repr(raw.headers) + repr(raw.params) + raw.url
    assert "s2-secret-value" not in recorded
    assert "ABC-123" not in recorded


@pytest.mark.parametrize(("status", "message"), [(401, "refused"), (404, "no ESPN league")])
def test_espn_access_errors_are_explained(status: int, message: str) -> None:
    with pytest.raises(espn_league.EspnAccessError, match=message):
        espn_league.fetch_league(mock_client(status, {}, []), 2027, "555", CREDS)


def test_sleeper_summary_maps_season_and_finds_my_roster() -> None:
    league = {
        "league_id": "9",
        "name": "Test League",
        "season": "2026",
        "sport": "nba",
        "total_rosters": 12,
    }
    rosters = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 4, "owner_id": "me"}]
    summary = sleeper_league.summarize(league, rosters, "me")
    assert summary.season == 2027
    assert summary.my_roster_id == "4"
    assert summary.team_count == 12


def test_sleeper_rejects_non_nba_leagues() -> None:
    with pytest.raises(sleeper_league.SleeperLookupError, match="not NBA"):
        sleeper_league.summarize({"season": "2026", "sport": "nfl"}, [], None)


def test_sleeper_unknown_league_is_an_error() -> None:
    client = _http.make_client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"null"))
    )
    with pytest.raises(sleeper_league.SleeperLookupError, match="nothing found"):
        sleeper_league.fetch_league(client, "does-not-exist")
