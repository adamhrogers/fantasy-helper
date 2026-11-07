"""The registry's guarantees and the daily run, against Postgres and the recorded fixtures."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from fantasy_helper.adapters import _http
from fantasy_helper.domain.facts import TOKEN_BUDGET
from fantasy_helper.domain.league import LeagueFormat, Platform
from fantasy_helper.domain.recommendations import (
    Action,
    CallInputs,
    CallType,
    Claim,
    Move,
    OutcomeWindow,
    Recommendation,
)
from fantasy_helper.pipeline.daily import run_daily
from fantasy_helper.pipeline.lock_calls import ENGINE_PRODUCER
from fantasy_helper.pipeline.weekly import run_weekly
from fantasy_helper.registry.calls import TipOffPassedError, log_call
from fantasy_helper.storage.repositories import leagues
from fantasy_helper.storage.tables import FactSheetRow, LeaguePlayerValueRow, RecommendationRow
from tests.integration.test_ingest import (
    KEY,
    NOW,
    FixedClock,
    count,
    seed_snapshots,
    serve_fixtures,
    setup_owner,
)

pytestmark = pytest.mark.db

TIP = NOW + timedelta(hours=8)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_sleep", lambda _seconds: None)


@pytest.fixture
def session(engine):  # type: ignore[no-untyped-def]
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def call(league_id, user_id, text: str = "Lock X's 30.0") -> Recommendation:  # type: ignore[no-untyped-def]
    claim = Claim(text=text, moves=(Move(action=Action.LOCK, player_id=uuid4()),), act_by=TIP)
    return Recommendation(
        created_at=NOW, league_id=league_id, user_id=user_id, platform=Platform.SLEEPER,
        format=LeagueFormat.LOCK_IN, call_type=CallType.LOCK_OR_PASS, claim=claim,
        expected_value=30.0, confidence=0.7, baseline_call=claim,
        inputs=CallInputs(projection_snapshot_ids=(), settings_version=1),
        producer=ENGINE_PRODUCER, outcome_window=OutcomeWindow(start=TIP, end=TIP + timedelta(2)),
        first_tip_utc=TIP, detail={"score": 30.0},
    )  # fmt: skip


def test_a_call_is_logged_once_and_never_edited(session: Session) -> None:
    owner = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]

    first, created = log_call(session, call(league.id, owner.id), "k1", now=NOW)
    assert created
    again, created = log_call(session, call(league.id, owner.id, "a different call"), "k1", now=NOW)
    assert not created
    assert (again.id, again.claim.text) == (first.id, "Lock X's 30.0")  # the first one stands
    assert count(session, RecommendationRow.id) == 1

    for statement in (
        update(RecommendationRow).values(confidence=0.9),
        delete(RecommendationRow),
    ):
        with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
            session.execute(statement)  # the database itself refuses


def test_calls_are_logged_before_tip_off_or_not_at_all(session: Session) -> None:
    owner = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]
    with pytest.raises(TipOffPassedError):
        log_call(session, call(league.id, owner.id), "k2", now=TIP)
    with pytest.raises(ValidationError, match="before tip-off"):
        Recommendation.model_validate(call(league.id, owner.id).model_dump() | {"created_at": TIP})
    assert count(session, RecommendationRow.id) == 0


def test_the_daily_run_logs_each_call_once(session: Session, tmp_path: Path) -> None:
    owner = setup_owner(session)
    sleeper = next(
        lg for lg in leagues.list_for_user(session, owner.id) if lg.platform is Platform.SLEEPER
    )
    leagues.upsert(session, sleeper.model_copy(update={"my_team_external_id": "1"}))
    store = seed_snapshots(tmp_path / "raw")
    client = _http.make_client(transport=httpx.MockTransport(serve_fixtures))

    result = run_daily(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    (daily,) = result.leagues  # only the lock-in league gets lock-or-pass calls
    assert daily.failure is None
    assert daily.report is not None
    assert daily.report.engine_only
    assert (
        daily.calls > 0
    )  # the recorded week has open decisions, so the rerun check means something
    assert daily.new_calls == daily.calls == count(session, RecommendationRow.id)

    # The box score check ran: the recorded Sleeper weeks compare (the ESPN fixtures' rosters
    # carry no stats, so there's nothing settled to compare there).
    (sleeper_check,) = [c for c in result.checks if c.league.platform is Platform.SLEEPER]
    assert sleeper_check.method == "one game a week"
    assert sleeper_check.agreement.total > 0

    again = run_daily(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert again.leagues[0].new_calls == 0  # a rerun logs nothing new
    assert again.leagues[0].calls == daily.calls
    assert count(session, RecommendationRow.id) == daily.calls


def test_the_weekly_run_builds_every_league_and_stores_its_facts(
    session: Session, tmp_path: Path
) -> None:
    owner = setup_owner(session)
    for lg in leagues.list_for_user(session, owner.id):
        team = "1" if lg.platform is Platform.SLEEPER else "13"
        leagues.upsert(session, lg.model_copy(update={"my_team_external_id": team}))
    store = seed_snapshots(tmp_path / "raw")
    client = _http.make_client(transport=httpx.MockTransport(serve_fixtures))

    result = run_weekly(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert len(result.leagues) == 3
    assert all(lg.failure is None for lg in result.leagues), [lg.failure for lg in result.leagues]
    for built in result.leagues:
        assert built.sheet is not None
        assert built.sheet.approx_tokens() < TOKEN_BUDGET
        assert built.report is not None
    # Mid-week (the clock says Friday): the points league banks Monday to Thursday from the
    # stored lineups and our box scores, so it can give a win probability.
    points = next(lg for lg in result.leagues if lg.league.name == "Points")
    assert points.sheet is not None
    assert points.sheet.matchup is not None
    assert points.sheet.matchup.my_banked is not None
    assert points.sheet.matchup.win_probability is not None
    sheets = count(session, FactSheetRow.id)
    values = count(session, LeaguePlayerValueRow.player_id)
    calls = count(session, RecommendationRow.id)
    assert sheets == 3
    assert values > 0

    again = run_weekly(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert all(lg.new_calls == 0 for lg in again.leagues)  # calls are logged once
    assert count(session, FactSheetRow.id) == sheets  # the same facts aren't stored twice
    assert count(session, LeaguePlayerValueRow.player_id) == values  # replaced, not appended
    assert count(session, RecommendationRow.id) == calls
