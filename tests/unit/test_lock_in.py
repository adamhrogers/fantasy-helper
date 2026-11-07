"""Lock-or-pass math: hand-checked cases plus properties that must always hold."""

from datetime import date
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fantasy_helper.domain.calendar import sleeper_week_bounds, sleeper_week_of
from fantasy_helper.domain.league import ScoringBonus, ScoringRules, ScoringType
from fantasy_helper.domain.recommendations import Action
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import (
    DEFAULT_CV,
    GameDistribution,
    expected_bonus,
    game_distribution,
    league_cv,
    shrunk_sd,
)
from fantasy_helper.engine.lock_in import (
    continuation_values,
    decide,
    expected_max,
    value_of_waiting,
)

OPENING = date(2026, 10, 20)  # a Tuesday


def test_sleeper_weeks_run_monday_to_sunday() -> None:
    assert sleeper_week_bounds(OPENING, 1) == (date(2026, 10, 20), date(2026, 10, 25))
    assert sleeper_week_bounds(OPENING, 2) == (date(2026, 10, 26), date(2026, 11, 1))
    assert sleeper_week_bounds(OPENING, 3) == (date(2026, 11, 2), date(2026, 11, 8))
    assert sleeper_week_of(OPENING, date(2026, 11, 6)) == 3
    assert sleeper_week_of(OPENING, date(2026, 10, 25)) == 1
    assert sleeper_week_of(OPENING, date(2026, 10, 26)) == 2


@given(st.integers(min_value=0, max_value=200))
def test_every_day_is_in_the_week_it_maps_to(offset: int) -> None:
    day = date.fromordinal(OPENING.toordinal() + offset)
    start, end = sleeper_week_bounds(OPENING, sleeper_week_of(OPENING, day))
    assert start <= day <= end


def test_the_last_game_counts_even_if_he_sits() -> None:
    # One game left that he's expected to miss: waiting is worth 0, so lock anything positive.
    decision = decide(12.0, [GameDistribution(mean=0.0, sd=0.0, play_probability=0.0)])
    assert decision is not None
    assert (decision.action, decision.value_of_waiting) == (Action.LOCK, 0.0)


def test_no_games_left_means_nothing_to_decide() -> None:
    assert decide(30.0, []) is None


def test_a_hand_checked_two_game_week() -> None:
    # Games left: 30 ± 0 (certain), then 20 ± 0. V(last) = 20; V(first) = max(30, 20) = 30.
    games = [GameDistribution(30.0, 0.0), GameDistribution(20.0, 0.0)]
    assert continuation_values(games) == [30.0, 20.0]
    assert decide(29.0, games).action is Action.PASS  # type: ignore[union-attr]
    assert decide(31.0, games).action is Action.LOCK  # type: ignore[union-attr]


def test_expected_max_matches_the_closed_form() -> None:
    # E[max(N(25, 5), 25)] = 25 + 5 * pdf(0) = 25 + 5 / sqrt(2 pi) ≈ 26.995
    assert expected_max(GameDistribution(25.0, 5.0), 25.0) == pytest.approx(26.9947, abs=1e-3)


games = st.lists(
    st.builds(
        GameDistribution,
        mean=st.floats(min_value=0, max_value=70),
        sd=st.floats(min_value=0, max_value=20),
        play_probability=st.floats(min_value=0, max_value=1),
    ),
    min_size=1,
    max_size=4,
)


@given(games)
def test_more_games_never_make_waiting_worth_less(week: list[GameDistribution]) -> None:
    values = continuation_values(week)
    assert all(a >= b - 1e-9 for a, b in pairwise(values))
    assert values[0] >= week[-1].expected - 1e-9


@given(games, st.floats(min_value=0, max_value=80), st.floats(min_value=0, max_value=20))
def test_a_higher_score_never_flips_lock_to_pass(
    week: list[GameDistribution], score: float, extra: float
) -> None:
    low, high = decide(score, week), decide(score + extra, week)
    assert low is not None
    assert high is not None
    if low.action is Action.LOCK:
        assert high.action is Action.LOCK
    assert 0.0 <= low.confidence <= 1.0
    assert value_of_waiting(week) == pytest.approx(low.value_of_waiting)


def test_spreads_shrink_toward_the_prior() -> None:
    assert shrunk_sd([], 30.0, 0.3) == pytest.approx(9.0)
    tight = shrunk_sd([30.0, 30.5, 29.5, 30.0, 30.2], 30.0, 0.3)
    assert 0.5 < tight < 9.0  # pulled up from the data's tiny spread, not all the way


def test_league_cv_needs_enough_players() -> None:
    assert league_cv([[10.0, 20.0, 30.0, 25.0, 15.0]]) == DEFAULT_CV
    many = [[20.0 + i, 30.0 + i, 25.0, 35.0, 15.0 + i] for i in range(40)]  # means 25 to 53
    assert 0.1 < league_cv(many) < 0.5


def test_a_leagues_threshold_bonuses_add_their_chance() -> None:
    rules = ScoringRules(
        scoring_type=ScoringType.POINTS,
        weights={Stat.POINTS: 1.0},
        bonuses=(ScoringBonus(stat=Stat.POINTS, threshold=40, points=2),),
    )
    # Projected 40 points with spread 5: half the time he reaches 40, so +1 expected.
    assert expected_bonus({Stat.POINTS: 40.0}, rules, {Stat.POINTS: 5.0}) == pytest.approx(1.0)
    dist = game_distribution(
        {Stat.POINTS: 40.0}, rules, history=[], stat_sds={Stat.POINTS: 5.0}, prior_cv=0.3
    )
    assert dist.mean == pytest.approx(41.0)
    sitting = game_distribution({}, rules, history=[], stat_sds={}, prior_cv=0.3)
    assert sitting.play_probability == 0.0
