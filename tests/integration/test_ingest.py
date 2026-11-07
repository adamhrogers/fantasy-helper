"""The whole of `fh ingest`, end to end, on the recorded fixtures and a real database."""

import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from fantasy_helper.adapters import _http
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.ports import Alert, AlertPriority
from fantasy_helper.domain.runs import FailureKind, RunKind
from fantasy_helper.domain.users import EspnCredentials, Role, User
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.pipeline.ingest import phone_summary, run_ingest
from fantasy_helper.storage import crypto
from fantasy_helper.storage.raw_store import LocalRawStore
from fantasy_helper.storage.repositories import credentials, leagues, nba, users
from fantasy_helper.storage.tables import (
    InjuryObservationRow,
    LeagueSettingsVersionRow,
    NbaGameRow,
    PlayerGameStatRow,
    PlayerRow,
    ProjectionValueRow,
    RosterEntryRow,
    TransactionRow,
)
from tests.contract.conftest import RECORDED

pytestmark = pytest.mark.db

KEY = crypto.load_key(crypto.generate_key())
NOW = datetime(2026, 11, 6, 17, 0, tzinfo=UTC)
SNAPSHOT_AT = datetime(2026, 11, 6, 11, 30, tzinfo=UTC)
ESPN_LEAGUES = {"111": "espn_points", "222": "espn_categories"}
SLEEPER_LEAGUE = "333"
FREE_AGENT = "990001"


class RecordingNotifier:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_sleep", lambda _seconds: None)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def seed_snapshots(root: Path) -> LocalRawStore:
    store = LocalRawStore(root)
    public = {
        ("espn", "players_projections"): ("espn_public/players_projections.json", ""),
        ("espn", "game_logs"): ("espn_public/game_logs.json", ""),
        ("espn", "pro_schedule"): ("espn_public/pro_schedule.json", ""),
        ("sleeper", "players"): ("sleeper_public/players.json", ""),
        ("sleeper", "state"): ("sleeper_public/state.json", ""),
        ("sleeper", "projections_week"): (
            "sleeper_public/projections_week.json",
            "https://api.sleeper.com/projections/nba/2026/3",
        ),
    }
    for (source, dataset), (path, url) in public.items():
        body = (RECORDED / path).read_bytes()
        if dataset == "players":  # the recorded subset is all rostered: add one free agent
            players = json.loads(body)
            players[FREE_AGENT] = {
                "player_id": FREE_AGENT, "full_name": "Test Free Agent", "team": "SAS",
                "position": "SG", "fantasy_positions": ["SG"], "active": True,
                "birth_date": "2004-01-02",
            }  # fmt: skip
            body = json.dumps(players).encode()
        store.put(
            source=source, dataset=dataset, fetched_at=SNAPSHOT_AT, body=body, meta={"url": url}
        )
    return store


def serve_fixtures(request: httpx.Request) -> httpx.Response:
    url, params = str(request.url), request.url.params
    if "lm-api-reads.fantasy.espn.com" in url:
        league = re.search(r"/leagues/(\d+)", url)
        assert league, url
        folder = ESPN_LEAGUES[league.group(1)]
        views = set(params.get_list("view"))
        if "mTransactions2" in views:
            by_period = json.loads((RECORDED / folder / "transactions.json").read_text())[
                "by_scoring_period"
            ]
            return httpx.Response(
                200, json=by_period.get(params["scoringPeriodId"], {"transactions": []})
            )
        if views == {"mRoster"}:  # one day's lineups (scoringPeriodId): the recorded day
            name = "lineups" if (RECORDED / folder / "lineups.json").exists() else "teams_rosters"
            return httpx.Response(200, content=(RECORDED / folder / f"{name}.json").read_bytes())
        datasets = {"mSettings": "settings", "mTeam": "teams_rosters", "mMatchupScore": "matchups"}
        dataset = next(datasets[v] for v in views if v in datasets)
        return httpx.Response(200, content=(RECORDED / folder / f"{dataset}.json").read_bytes())
    path = request.url.path
    for pattern, fixture in (
        (rf"/league/{SLEEPER_LEAGUE}$", "league"), (r"/users$", "users"), (r"/rosters$", "rosters"),
        (r"/matchups/(\d+)$", "matchups_{}"), (r"/transactions/(\d+)$", "transactions_{}"),
    ):  # fmt: skip
        match = re.search(pattern, path)
        if match:
            name = fixture.format(*match.groups())
            return httpx.Response(
                200, content=(RECORDED / "sleeper_points" / f"{name}.json").read_bytes()
            )
    raise AssertionError(f"unexpected request: {url}")


def setup_owner(session: Session) -> User:
    owner = users.create(
        session,
        User(email=f"{uuid.uuid4().hex[:8]}@example.com", display_name="Adam",
             timezone="America/New_York", role=Role.OWNER),
    )  # fmt: skip
    credentials.save_espn(
        session, owner.id, EspnCredentials(espn_s2=SecretStr("s2"), swid=SecretStr("{X}")), KEY
    )
    for external_id, name in (("111", "Points"), ("222", "Categories")):
        espn = League(
            user_id=owner.id,
            platform=Platform.ESPN,
            external_id=external_id,
            season=2027,
            name=name,
        )
        leagues.upsert(session, espn)
    sleeper = League(
        user_id=owner.id,
        platform=Platform.SLEEPER,
        external_id=SLEEPER_LEAGUE,
        season=2027,
        name="Sleeper",
    )
    leagues.upsert(session, sleeper)
    return owner


def count(session: Session, column: Any) -> int:
    return int(session.scalar(select(func.count(column))) or 0)


def test_ingest_loads_everything_and_is_idempotent(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Small batches, so the recorded box scores span several INSERT statements: the live
    # backfill (6,935 rows) once exceeded Postgres's 65,535-parameter limit in one statement.
    monkeypatch.setattr(nba, "GAME_LOG_CHUNK", 7)
    owner = setup_owner(session)
    store = seed_snapshots(tmp_path / "raw")
    client = _http.make_client(transport=httpx.MockTransport(serve_fixtures))

    phone = RecordingNotifier()
    events = RunEvents(RunKind.INGEST, phone, label="Ingest")
    report = run_ingest(
        session, client, store=store, user=owner, key=KEY, clock=FixedClock(), events=events
    )
    assert [a.title for a in phone.alerts] == [
        "Ingest: League data fetched", "Ingest: Public data loaded", "Ingest: Leagues stored",
    ]  # fmt: skip
    assert {a.priority for a in phone.alerts} == {AlertPriority.LOW}
    names = [lg.name for lg in leagues.list_for_user(session, owner.id)]
    assert names
    assert not any(name in a.message for a in phone.alerts for name in names)
    detail, problems = phone_summary(report, attempts=1)
    assert detail.startswith("Leagues: 3 of 3 loaded.")
    # ("Sleeper" is both this test league's name and a platform, so it's left out of the check.)
    private = [n for n in names if n != "Sleeper"]
    assert not any(name in detail + " ".join(problems) for name in private)

    assert report.errors == []
    by_name = {lg.league.name: lg for lg in report.leagues}
    assert set(by_name) == {"Points", "Categories", "Sleeper"}
    points, sleeper = by_name["Points"], by_name["Sleeper"]
    assert (points.roster_entries, points.matchups, points.transactions) == (155, 90, 118)
    assert points.trades_without_players == 4  # ESPN hides other teams' proposals
    assert points.new_settings
    assert points.settings_version == 1
    assert all(
        lg.unresolved_players == 0 for lg in report.leagues
    )  # every rostered player resolves
    assert sleeper.matchups == 15
    raw_players = json.loads((RECORDED / "sleeper_public/players.json").read_text())
    rostered = {str(p) for r in json.loads((RECORDED / "sleeper_points/rosters.json").read_text())
                for p in r.get("players") or []}  # fmt: skip
    expected_free = 1 + sum(
        1 for pid, p in raw_players.items() if p.get("active") and pid not in rostered
    )
    assert sleeper.free_agents == expected_free  # every active player not on a roster
    assert sleeper.rostered_sleeper > 100
    assert sleeper.rostered_sleeper_linked / sleeper.rostered_sleeper > 0.8
    assert report.games > 0
    assert report.projection_snapshots == 2
    assert report.projection_rows > 0
    assert report.injury_changes > 0
    # The recorded schedule keeps 6 of 30 teams, so only those teams' games have box scores.
    assert report.game_log_payloads == 1
    assert report.game_log_rows > 20
    assert report.game_logs_unresolved == 0
    assert report.game_logs_unknown_game > 0
    assert count(session, PlayerGameStatRow.player_id) == report.game_log_rows
    assert report.database_bytes > 0

    players = count(session, PlayerRow.id)
    rows = (
        count(session, RosterEntryRow.id), count(session, TransactionRow.id),
        count(session, ProjectionValueRow.id), count(session, InjuryObservationRow.id),
        count(session, LeagueSettingsVersionRow.id), count(session, NbaGameRow.id),
    )  # fmt: skip

    again = run_ingest(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert again.errors == []
    assert again.crosswalk.created == 0
    assert not again.crosswalk.linked
    assert (again.projection_snapshots, again.projection_rows, again.injury_changes) == (0, 0, 0)
    assert (again.game_log_payloads, again.game_log_rows) == (0, 0)
    assert not any(lg.new_settings for lg in again.leagues)
    assert count(session, PlayerRow.id) == players
    assert (
        count(session, RosterEntryRow.id), count(session, TransactionRow.id),
        count(session, ProjectionValueRow.id), count(session, InjuryObservationRow.id),
        count(session, LeagueSettingsVersionRow.id), count(session, NbaGameRow.id),
    ) == rows  # fmt: skip


def test_one_league_failing_never_stops_the_others(session: Session, tmp_path: Path) -> None:
    owner = setup_owner(session)
    store = seed_snapshots(tmp_path / "raw")

    def categories_league_down(request: httpx.Request) -> httpx.Response:
        if "/leagues/222" in str(request.url):
            return httpx.Response(503)
        return serve_fixtures(request)

    client = _http.make_client(transport=httpx.MockTransport(categories_league_down))
    report = run_ingest(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    by_name = {lg.league.name: lg for lg in report.leagues}
    assert by_name["Categories"].failure is FailureKind.SERVER_ERROR  # retryable
    assert by_name["Points"].failure is None
    assert by_name["Sleeper"].failure is None
    assert by_name["Points"].roster_entries == 155
    detail, problems = phone_summary(report, attempts=3)
    assert detail.startswith("Leagues: 2 of 3 loaded.")
    assert problems[0] == "1 ESPN league failed: the source's servers erred, after 3 attempts"
