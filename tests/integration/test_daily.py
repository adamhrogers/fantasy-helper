"""The registry's guarantees and the daily run, against Postgres and the recorded fixtures."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, insert, update
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
from fantasy_helper.domain.reports import CardKind, ReportKind
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.facts.daily import load_daily
from fantasy_helper.pipeline import daily as daily_pipeline
from fantasy_helper.pipeline.daily import daily_summary, run_daily
from fantasy_helper.pipeline.lock_calls import ENGINE_PRODUCER
from fantasy_helper.pipeline.weekly import run_weekly
from fantasy_helper.registry.calls import TipOffPassedError, log_call
from fantasy_helper.storage.repositories import leagues
from fantasy_helper.storage.repositories import reports as saved_reports
from fantasy_helper.storage.tables import (
    FactSheetRow,
    LeaguePlayerValueRow,
    RecommendationRow,
    ReportRow,
)
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
    for lg in leagues.list_for_user(session, owner.id):
        team = "1" if lg.platform is Platform.SLEEPER else "13"
        leagues.upsert(session, lg.model_copy(update={"my_team_external_id": team}))
    store = seed_snapshots(tmp_path / "raw")
    client = _http.make_client(transport=httpx.MockTransport(serve_fixtures))

    result = run_daily(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert len(result.leagues) == 3  # every league gets a daily report now, not only lock-in
    assert all(lg.failure is None for lg in result.leagues), [lg.failure for lg in result.leagues]
    (daily,) = [lg for lg in result.leagues if lg.lock_in]  # only this one gets lock-or-pass calls
    assert daily.league.platform is Platform.SLEEPER
    assert daily.failure is None
    assert daily.report is not None
    assert daily.report.engine_only
    assert (
        daily.calls > 0
    )  # the recorded week has open decisions, so the rerun check means something
    assert daily.new_calls == daily.calls == count(session, RecommendationRow.id)
    assert [lg.calls for lg in result.leagues if not lg.lock_in] == [0, 0]

    # The lock cards are what they were: one per logged call, first, then the lock-in recap; the
    # "what happened" cards come after them.
    kinds = [c.kind for c in daily.report.cards]
    locks = kinds.count(CardKind.LOCK_OR_PASS)
    assert locks == daily.calls
    assert kinds[:locks] == [CardKind.LOCK_OR_PASS] * locks
    assert daily.report.cards[locks].id == "lock-in-recap"
    assert any(c.id.startswith("daily-") for c in daily.report.cards[locks + 1 :])
    assert daily.recap_cards > 0
    for other in (lg for lg in result.leagues if not lg.lock_in):
        assert other.report is not None
        assert CardKind.LOCK_OR_PASS not in {c.kind for c in other.report.cards}
        assert other.recap_cards == len(other.report.cards)  # all of it is the recap

    # The box score check ran: the recorded Sleeper weeks compare (the ESPN fixtures' rosters
    # carry no stats, so there's nothing settled to compare there).
    (sleeper_check,) = [c for c in result.checks if c.league.platform is Platform.SLEEPER]
    assert sleeper_check.method == "one game a week"
    assert sleeper_check.agreement.total > 0

    # Each league's report is saved with its calls, and reads back exactly as it was built.
    stored = saved_reports.for_user(session, owner.id, ReportKind.DAILY)
    assert {r.league_id for r in stored} == {lg.league.id for lg in result.leagues}
    stored_sleeper = next(r for r in stored if r.league_id == daily.league.id)
    assert stored_sleeper.cards == daily.report.cards
    assert stored_sleeper.for_date == daily.report.for_date
    saved_ids = {r.league_id: r.id for r in stored}

    again = run_daily(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    (daily_again,) = [lg for lg in again.leagues if lg.lock_in]
    assert daily_again.new_calls == 0  # a rerun logs nothing new
    assert daily_again.calls == daily.calls
    assert count(session, RecommendationRow.id) == daily.calls
    # A rerun replaces the day's report: still one row per league, still the first ids, and the
    # report the run hands on (to the push, the summary) carries that stored id, not a new one.
    assert count(session, ReportRow.id) == 3
    stored_again = saved_reports.for_user(session, owner.id, ReportKind.DAILY)
    assert {r.league_id: r.id for r in stored_again} == saved_ids
    assert all(
        lg.report is not None and lg.report.id == saved_ids[lg.league.id] for lg in again.leagues
    )
    assert daily.report.id == saved_ids[daily.league.id]


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
    assert count(session, ReportRow.id) == 3  # each league's overview is saved for the app
    saved = saved_reports.for_user(session, owner.id, ReportKind.WEEKLY)
    assert {r.league_id for r in saved} == {lg.league.id for lg in result.leagues}
    stored_ids = {r.league_id: r.id for r in saved}
    assert all(
        lg.report is not None and lg.report.id == stored_ids[lg.league.id] for lg in result.leagues
    )  # the reports handed on carry the stored ids

    again = run_weekly(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert all(lg.new_calls == 0 for lg in again.leagues)  # calls are logged once
    assert count(session, FactSheetRow.id) == sheets  # the same facts aren't stored twice
    assert count(session, LeaguePlayerValueRow.player_id) == values  # replaced, not appended
    categories = next(lg for lg in again.leagues if lg.league.name == "Categories")
    assert categories.sheet is not None
    assert all(p.value_over_replacement is None for p in categories.sheet.my_players)
    assert count(session, RecommendationRow.id) == calls
    assert count(session, ReportRow.id) == 3  # replaced, not appended
    assert all(
        lg.report is not None and lg.report.id == stored_ids[lg.league.id] for lg in again.leagues
    )  # and a rerun still hands on the first ids


def test_merging_a_player_clears_his_cached_values(session: Session) -> None:
    from fantasy_helper.domain.player import IdSource
    from fantasy_helper.domain.records import PlayerKey
    from fantasy_helper.storage.repositories import players
    from fantasy_helper.storage.tables import PlayerRow

    owner = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]
    provisional, real = uuid4(), uuid4()
    session.add_all([PlayerRow(id=provisional, full_name="P"), PlayerRow(id=real, full_name="P")])
    session.flush()
    session.execute(insert(LeaguePlayerValueRow).values(
        league_id=league.id, player_id=provisional, as_of=NOW, slot="FA", games_left=2,
        startable_left=0, per_game=20.0,
    ))  # fmt: skip
    key = PlayerKey(source=IdSource.SLEEPER, external_id="1")
    players.merge(session, provisional, real, key=key, method="override")
    assert count(session, LeaguePlayerValueRow.player_id) == 0  # no FK error, cache cleared


def test_a_failed_lineup_fetch_never_fails_the_league(session: Session, tmp_path: Path) -> None:
    owner = setup_owner(session)
    store = seed_snapshots(tmp_path / "raw")

    def lineups_down(request: httpx.Request) -> httpx.Response:
        if set(request.url.params.get_list("view")) == {"mRoster"}:
            return httpx.Response(503)
        return serve_fixtures(request)

    from fantasy_helper.pipeline.ingest import run_ingest

    client = _http.make_client(transport=httpx.MockTransport(lineups_down))
    run_ingest(session, client, store=store, user=owner, key=KEY, clock=FixedClock())  # schedule
    report = run_ingest(session, client, store=store, user=owner, key=KEY, clock=FixedClock())
    assert all(lg.failure is None for lg in report.leagues)


def run_all(session: Session, tmp_path: Path) -> daily_pipeline.DailyResult:
    owner = setup_owner(session)
    for lg in leagues.list_for_user(session, owner.id):
        team = "1" if lg.platform is Platform.SLEEPER else "13"
        leagues.upsert(session, lg.model_copy(update={"my_team_external_id": team}))
    store = seed_snapshots(tmp_path / "raw")
    client = _http.make_client(transport=httpx.MockTransport(serve_fixtures))
    return run_daily(session, client, store=store, user=owner, key=KEY, clock=FixedClock())


def own_problems(result: daily_pipeline.DailyResult, attempts: int) -> list[str]:
    """The daily run's own lines (the fixtures' ingest and box-score notes are not under test)."""
    _, problems = daily_summary(result, attempts)
    return [
        p for p in problems if p.startswith(("recap cards", "the daily recap", "lock-in calls"))
    ]


def test_a_broken_recap_never_costs_the_lock_calls(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = load_daily

    def flaky(session: Session, league, **kwargs):  # type: ignore[no-untyped-def]
        if league.platform is Platform.SLEEPER:
            raise httpx.ConnectError("down")  # a failure a retry could fix
        return real(session, league, **kwargs)

    monkeypatch.setattr("fantasy_helper.pipeline.daily.load_daily", flaky)
    result = run_all(session, tmp_path)
    (lock,) = [lg for lg in result.leagues if lg.lock_in]
    assert lock.failure is None  # the league's report is fine
    assert lock.recap_failure is FailureKind.NETWORK
    assert lock.calls > 0
    assert lock.report is not None
    kinds = [c.kind for c in lock.report.cards]
    assert kinds.count(CardKind.LOCK_OR_PASS) == lock.calls  # the lock cards are all there
    assert not any(c.id.startswith("daily-") for c in lock.report.cards)  # and no recap
    assert count(session, RecommendationRow.id) == lock.calls
    assert result.retryable_failures() == 1  # a retry could build the recap
    assert own_problems(result, attempts=3) == [
        "recap cards failed for 1 Sleeper league (the lock calls are saved): couldn't connect, "
        "after 3 attempts"
    ]
    for other in (lg for lg in result.leagues if not lg.lock_in):
        assert other.recap_failure is None  # the other leagues are unaffected
        assert other.report is not None


def test_a_broken_lock_part_still_gives_the_recap_and_says_the_locks_are_missing(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(daily_pipeline, "_lock_in_cards", broken)
    result = run_all(session, tmp_path)
    (lock,) = [lg for lg in result.leagues if lg.lock_in]
    assert lock.failure is FailureKind.BUG
    assert (lock.calls, lock.new_calls) == (0, 0)
    assert lock.report is not None  # saved: the recap is still worth reading
    ids = [c.id for c in lock.report.cards]
    assert ids[0] == "lock-in-unavailable"  # never mistaken for "nothing to decide"
    assert any(i.startswith("daily-") for i in ids[1:])
    assert count(session, RecommendationRow.id) == 0
    (problem,) = own_problems(result, attempts=1)
    assert problem.startswith("lock-in calls failed for 1 Sleeper league: a bug in our code")


def test_a_league_whose_recap_breaks_has_no_report_and_the_others_still_do(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = load_daily

    def flaky(session: Session, league, **kwargs):  # type: ignore[no-untyped-def]
        if league.name == "Points":
            raise RuntimeError("boom")
        return real(session, league, **kwargs)

    monkeypatch.setattr("fantasy_helper.pipeline.daily.load_daily", flaky)
    result = run_all(session, tmp_path)
    broken = next(lg for lg in result.leagues if lg.league.name == "Points")
    assert broken.failure is FailureKind.BUG
    assert broken.report is None
    assert count(session, ReportRow.id) == 2  # the other two leagues
    assert len(result.reports) == 2
    assert own_problems(result, attempts=1) == [
        "the daily recap failed for 1 ESPN league: a bug in our code; not retried (retrying "
        "can't fix it)"
    ]
