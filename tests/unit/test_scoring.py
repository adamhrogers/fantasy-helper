"""Property tests for the scoring math (architecture §13: hypothesis for the engine)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fantasy_helper.domain.league import CategoryRule, ScoringBonus, ScoringRules, ScoringType
from fantasy_helper.domain.stats import (
    FIELD_GOALS_MISSED,
    FREE_THROWS_MISSED,
    THREES_MISSED,
    Stat,
    StatLine,
    add_weight,
)
from fantasy_helper.engine.scoring import (
    NotAPointsLeagueError,
    bonus_points,
    game_points,
    linear_points,
    missing_stats,
    projected_points,
)

counts = st.integers(min_value=0, max_value=80).map(float)
stat_lines = st.dictionaries(st.sampled_from(list(Stat)), counts, max_size=len(Stat))
# Weights in half points, as platforms use; at least one non-zero so the rules are valid.
weights = st.dictionaries(
    st.sampled_from(list(Stat)),
    st.integers(min_value=-8, max_value=8).map(lambda n: n / 2),
    min_size=1,
).filter(lambda w: any(w.values()))


def points_league(w: StatLine, bonuses: tuple[ScoringBonus, ...] = ()) -> ScoringRules:
    return ScoringRules(scoring_type=ScoringType.POINTS, weights=w, bonuses=bonuses)


@given(stat_lines, weights, weights)
def test_points_are_linear_in_the_weights(stats: StatLine, w1: StatLine, w2: StatLine) -> None:
    combined = {s: w1.get(s, 0.0) + w2.get(s, 0.0) for s in w1.keys() | w2.keys()}
    if not any(combined.values()):
        return
    total = linear_points(stats, points_league(combined))
    assert total == pytest.approx(
        linear_points(stats, points_league(w1)) + linear_points(stats, points_league(w2))
    )


@given(stat_lines, weights, st.integers(min_value=0, max_value=5))
def test_points_scale_with_the_stats(stats: StatLine, w: StatLine, k: int) -> None:
    scaled = {s: v * k for s, v in stats.items()}
    rules = points_league(w)
    assert linear_points(scaled, rules) == pytest.approx(k * linear_points(stats, rules))


@given(weights)
def test_a_game_sat_out_scores_zero_even_with_bonuses(w: StatLine) -> None:
    rules = points_league(w, (ScoringBonus(stat=Stat.POINTS, threshold=40, points=2),))
    assert game_points({}, rules) == 0.0


@given(counts, counts)
def test_more_points_never_lose_a_bonus(a: float, b: float) -> None:
    rules = points_league(
        {Stat.POINTS: 1.0},
        (
            ScoringBonus(stat=Stat.POINTS, threshold=40, points=2),
            ScoringBonus(stat=Stat.POINTS, threshold=50, points=2),
        ),
    )
    low, high = sorted((a, b))
    assert bonus_points({Stat.POINTS: low}, rules) <= bonus_points({Stat.POINTS: high}, rules)


def test_projections_leave_bonuses_out() -> None:
    rules = points_league(
        {Stat.POINTS: 1.0}, (ScoringBonus(stat=Stat.POINTS, threshold=40, points=2),)
    )
    assert projected_points({Stat.POINTS: 45.0}, rules) == 45.0
    assert game_points({Stat.POINTS: 45.0}, rules) == 47.0


def test_missing_weighted_stats_are_reported() -> None:
    rules = points_league({Stat.POINTS: 1.0, Stat.ASSISTS: 2.0, Stat.REBOUNDS: 0.0})
    assert missing_stats({Stat.POINTS: 10.0}, rules) == {Stat.ASSISTS}


def test_categories_leagues_have_no_fantasy_points() -> None:
    rules = ScoringRules(
        scoring_type=ScoringType.CATEGORIES,
        categories=(CategoryRule(name="PTS", stat=Stat.POINTS),),
    )
    with pytest.raises(NotAPointsLeagueError):
        game_points({Stat.POINTS: 10.0}, rules)


@given(stat_lines, st.integers(min_value=-4, max_value=4).map(float))
def test_a_missed_shot_weight_scores_attempts_minus_makes(stats: StatLine, w: float) -> None:
    """A league weighting missed shots scores exactly w * (attempts - makes) for each kind."""
    for parts, attempts, makes in (
        (FIELD_GOALS_MISSED, Stat.FIELD_GOALS_ATTEMPTED, Stat.FIELD_GOALS_MADE),
        (FREE_THROWS_MISSED, Stat.FREE_THROWS_ATTEMPTED, Stat.FREE_THROWS_MADE),
        (THREES_MISSED, Stat.THREES_ATTEMPTED, Stat.THREES_MADE),
    ):
        weights: dict[Stat, float] = {Stat.POINTS: 1.0}
        add_weight(weights, parts, w)
        missed = stats.get(attempts, 0.0) - stats.get(makes, 0.0)
        expected = stats.get(Stat.POINTS, 0.0) + w * missed
        assert linear_points(stats, points_league(weights)) == pytest.approx(expected)
