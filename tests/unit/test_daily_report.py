"""Lock hints and the daily report's cards, from a hand-built sheet (no database)."""

from datetime import UTC, date, datetime
from uuid import uuid4

from fantasy_helper.adapters.sleeper import mapper as sleeper
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.reports import CardKind
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.lock_in import decide
from fantasy_helper.facts.lock_in import (
    GameScore,
    LockInSheet,
    ProjectionSource,
    SleeperShows,
    StarterWeek,
    UpcomingGame,
    sleeper_shows,
)
from fantasy_helper.pipeline.daily import open_decisions
from fantasy_helper.pipeline.lock_calls import dedupe_key, lock_or_pass_call
from fantasy_helper.reports.daily import LOCKS_NOTE, lock_in_report
from fantasy_helper.reports.html import render
from tests.contract.conftest import recorded

NOW = datetime(2026, 11, 6, 17, 0, tzinfo=UTC)  # Friday, noon ET
TONIGHT = datetime(2026, 11, 7, 1, 0, tzinfo=UTC)  # Friday, 8 p.m. ET
SUNDAY = datetime(2026, 11, 8, 23, 0, tzinfo=UTC)


def game(day: int, points: float, played: bool = True) -> GameScore:
    return GameScore(uuid4(), date(2026, 11, day), points, played)


def upcoming(tip: datetime, mean: float) -> UpcomingGame:
    return UpcomingGame(
        uuid4(), tip, tip.date(), GameDistribution(mean, 0.35 * mean), ProjectionSource.GAME
    )


def starter(name: str, latest: GameScore | None, earlier: tuple[GameScore, ...] = (),
            games: tuple[UpcomingGame, ...] = (), reported: float | None = None,
            closed: bool = False) -> StarterWeek:  # fmt: skip
    return StarterWeek(
        player_id=uuid4(), name=name, team="DEN", slot="UTIL", latest=latest, earlier=earlier,
        upcoming=games, reported_points=reported,
        sleeper_shows=sleeper_shows(reported, latest, earlier), window_closed=closed,
    )  # fmt: skip


def sheet(*starters: StarterWeek) -> LockInSheet:
    rules = sleeper.map_rules(recorded("sleeper_points/league.json")).rules
    league = League(user_id=uuid4(), platform=Platform.SLEEPER, external_id="1", season=2027,
                    name="Friends <League>", my_team_external_id="1")  # fmt: skip
    return LockInSheet(
        league=league, rules=rules, settings_version=1, for_date=date(2026, 11, 6), week=3,
        week_start=date(2026, 11, 2), week_end=date(2026, 11, 8),
        projection_snapshot_ids=(uuid4(),), prior_cv=0.385, starters=starters,
    )  # fmt: skip


def test_what_sleeper_shows_is_classified_for_diagnostics() -> None:
    mon, wed = game(2, 56.5), game(4, 29.5)
    assert sleeper_shows(29.5, wed, (mon,)) is SleeperShows.LATEST
    assert sleeper_shows(56.5, wed, (mon,)) is SleeperShows.EARLIER
    assert sleeper_shows(31.0, wed, (mon,)) is SleeperShows.NO_MATCH  # the stat feeds differ
    assert sleeper_shows(31.0, wed, ()) is SleeperShows.LATEST  # nothing earlier to be locked on
    assert sleeper_shows(None, wed, (mon,)) is SleeperShows.NO_MATCH


def test_every_open_decision_gets_a_card_because_locks_arent_visible() -> None:
    wed = game(4, 29.5)
    maybe = starter("Maybe Locked", wed, (game(2, 56.5),), (upcoming(SUNDAY, 30),), reported=56.5)
    sat_out = starter("Sat Out", game(4, 0, played=False), games=(upcoming(TONIGHT, 30),))
    closed = starter("Tipped", game(4, 40), games=(upcoming(SUNDAY, 30),), closed=True)
    done = starter("No Games Left", game(5, 33))
    assert [s.name for s in open_decisions([maybe, sat_out, closed, done])] == ["Maybe Locked"]


def test_cards_say_what_was_logged_and_when_to_decide() -> None:
    lock = starter("Big Night", game(4, 52.0), games=(upcoming(TONIGHT, 30), upcoming(SUNDAY, 28)))
    maybe = starter("Maybe Locked", game(4, 29.5), (game(2, 56.5),),
                    (upcoming(SUNDAY, 34),), reported=56.5)  # fmt: skip
    quiet = starter("No Games Left", game(5, 33))
    s = sheet(lock, maybe, quiet)
    calls = []
    for st in (lock, maybe):
        assert st.latest is not None
        decision = decide(st.latest.points, [g.distribution for g in st.upcoming])
        assert decision is not None
        calls.append((st, lock_or_pass_call(s, st, decision, user_id=s.league.user_id, now=NOW)))
    assert dedupe_key(lock) == f"lock_or_pass:{lock.player_id}:{lock.latest.game_id}"  # type: ignore[union-attr]

    report = lock_in_report(s, calls, user_id=s.league.user_id, generated_at=NOW)
    first, second, recap = report.cards
    assert report.engine_only
    assert first.kind is CardKind.LOCK_OR_PASS
    assert first.title == "Lock Big Night: 52.0"
    assert "Decide before Fri 8:00 PM ET." in first.body
    assert first.recommendation_id == calls[0][1].id
    assert calls[0][1].first_tip_utc == TONIGHT  # the deadline is his next tip-off
    assert "Sleeper" not in second.body  # its numbers don't show locks, so no hint
    assert recap.kind is CardKind.RECAP
    assert recap.body.startswith(LOCKS_NOTE)
    assert "No Games Left" in recap.body

    page = render([report], title="Daily report", league_names={str(s.league.id): s.league.name})
    assert "Lock Big Night: 52.0" in page
    assert "Friends &lt;League&gt;" in page  # escaped
