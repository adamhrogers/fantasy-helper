"""Fixes from the PR #3 review: each test names the failure it guards against."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from fantasy_helper.domain.league import AcquisitionLimits
from fantasy_helper.domain.player import Position
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.lock_in import week_value
from fantasy_helper.engine.schedule import PlayerWeek
from fantasy_helper.facts.availability import season_fallback
from fantasy_helper.facts.weekly import (
    WeekPlayer,
    _lockable,
    matchup_add_limit,
    period_started_before,
)
from fantasy_helper.pipeline.reconcile import unsettled_teams
from fantasy_helper.pipeline.weekly import protected_from_drops

NOW = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)  # Friday 3 p.m. ET
TODAY = date(2026, 11, 6)


def test_an_injured_player_without_a_game_projection_sits() -> None:
    hurt, healthy = uuid4(), uuid4()
    season = {Stat.POINTS: 20.0}
    assert season_fallback(season, hurt, {hurt}) == {}  # out: he sits
    assert season_fallback(season, healthy, {hurt}) == season


def week_player(season_per_game: float | None) -> WeekPlayer:
    pid = uuid4()
    return WeekPlayer(pid, "P", "DEN", (Position.C,), "C", PlayerWeek(pid, (Position.C,), ()),
                      season_per_game)  # fmt: skip


def test_players_without_a_season_average_are_never_dropped() -> None:
    returning_star = week_player(None)  # 2 games played, none left this week
    replaceable = week_player(12.0)
    good = week_player(40.0)
    vor = {returning_star.player_id: -20.0, replaceable.player_id: -3.0, good.player_id: 15.0}
    keep = protected_from_drops([returning_star, replaceable, good], vor)
    assert keep == {returning_star.player_id, good.player_id}


def test_the_add_budget_follows_the_matchup_length() -> None:
    rate = AcquisitionLimits(per_matchup_per_day=0.571)
    assert matchup_add_limit(rate, 7) == 4
    assert matchup_add_limit(rate, 6) == 3  # week 1: Tuesday to Sunday
    assert matchup_add_limit(AcquisitionLimits(per_matchup=5), 6) == 5
    assert matchup_add_limit(AcquisitionLimits(), 7) is None


def test_a_matchup_that_started_in_an_earlier_week_is_flagged() -> None:
    assert not period_started_before({14: 120.0, 16: 230.0}, 14)  # this week's days only
    assert period_started_before({7: 99.0, 14: 120.0}, 14)  # a two-week (All-Star) period
    assert not period_started_before({}, 14)


def test_a_lockable_big_game_counts_in_the_lock_in_projection() -> None:
    rest = [GameDistribution(30.0, 9.0)]
    without, _ = week_value(rest)
    with_latest, _ = week_value(rest, latest=52.0)
    assert with_latest >= 52.0 > without
    assert week_value([], latest=40.0) == (40.0, 0.0)  # no games left: his last game counts


def test_the_lock_window_closes_when_his_next_game_tips() -> None:
    pid = uuid4()
    by_day = {(pid, date(2026, 11, 4)): 52.0}
    tonight = datetime(2026, 11, 7, 0, 0, tzinfo=UTC)  # Friday 7 p.m. ET
    games = [(tonight, TODAY)]
    assert _lockable(pid, games, by_day, date(2026, 11, 2), TODAY, NOW) == 52.0
    later = datetime(2026, 11, 7, 1, 0, tzinfo=UTC)
    assert _lockable(pid, games, by_day, date(2026, 11, 2), TODAY, later) is None


def test_teams_with_games_after_our_latest_box_score_are_skipped() -> None:
    gid = uuid4()
    last_night = datetime(2026, 11, 6, 1, 0, tzinfo=UTC)
    tonight = datetime(2026, 11, 7, 0, 0, tzinfo=UTC)
    games = [
        (gid, last_night, date(2026, 11, 5), "BOS", "NYK"),  # box score not loaded yet
        (gid, tonight, TODAY, "DEN", "LAL"),  # not tipped at NOW
    ]
    assert unsettled_teams(games, TODAY, NOW) == {"BOS", "NYK"}
    assert unsettled_teams(games, TODAY, tonight) == {"BOS", "NYK", "DEN", "LAL"}


@pytest.mark.parametrize("status", ["out", "suspended"])
def test_sitting_statuses(status: str) -> None:
    from fantasy_helper.facts.availability import SITTING

    assert status in SITTING
    assert "day_to_day" not in SITTING  # questionable players are still projected to play
