"""The "what happened last night" math: hand-checked cases and properties. Generic data only."""

import math
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fantasy_helper.domain.league import (
    CategoryRule,
    ScoringBonus,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.stats import Stat, StatLine
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.recap import (
    GameValue,
    Performance,
    Pickup,
    category_baselines,
    category_standing,
    category_totals,
    compare_totals,
    expected_but_sat,
    expected_game,
    good_game_line,
    lineup_total,
    pickups_that_did_well,
    standouts,
    surprises,
)

POINTS = ScoringRules(
    scoring_type=ScoringType.POINTS,
    weights={Stat.POINTS: 1.0, Stat.REBOUNDS: 1.0, Stat.ASSISTS: 2.0},
    bonuses=(ScoringBonus(stat=Stat.POINTS, threshold=30, points=3.0),),
)
CATEGORIES = ScoringRules(
    scoring_type=ScoringType.CATEGORIES,
    categories=(
        CategoryRule(name="PTS", stat=Stat.POINTS),
        CategoryRule(name="TO", stat=Stat.TURNOVERS, higher_is_better=False),
        CategoryRule(name="FG%", ratio_of=(Stat.FIELD_GOALS_MADE, Stat.FIELD_GOALS_ATTEMPTED)),
    ),
)
# Four games, hand-checked: points 10, 20, 30, 40 (mean 25, sample sd sqrt(500/3) = 12.9099);
# turnovers 1..4 (mean 2.5, sd 1.29099); shooting 4/8, 6/10, 2/10, 8/12: 20 of 40 is 50%, so
# the makes above 50% are 0, +1, -3, +2 (mean 0, sample sd sqrt(14/3) = 2.1602).
LINES: list[StatLine] = [
    {Stat.POINTS: 10, Stat.TURNOVERS: 1, Stat.FIELD_GOALS_MADE: 4, Stat.FIELD_GOALS_ATTEMPTED: 8},
    {Stat.POINTS: 20, Stat.TURNOVERS: 2, Stat.FIELD_GOALS_MADE: 6, Stat.FIELD_GOALS_ATTEMPTED: 10},
    {Stat.POINTS: 30, Stat.TURNOVERS: 3, Stat.FIELD_GOALS_MADE: 2, Stat.FIELD_GOALS_ATTEMPTED: 10},
    {Stat.POINTS: 40, Stat.TURNOVERS: 4, Stat.FIELD_GOALS_MADE: 8, Stat.FIELD_GOALS_ATTEMPTED: 12},
]


def perf(
    name: str,
    value: float,
    *,
    played: bool = True,
    expected: tuple[float, float] | None = None,
    plays: float = 1.0,
    owner: str | None = "1",
    pid: UUID | None = None,
) -> Performance:
    return Performance(
        player_id=pid or uuid4(),
        name=name,
        nba_team="AAA",
        played=played,
        value=value if played else 0.0,
        line={},
        expected=GameDistribution(expected[0], expected[1], plays) if expected else None,
        owner=owner,
    )


# --------------------------------------------------------------------- a game's value


def test_a_points_game_is_the_leagues_points_with_its_bonus() -> None:
    value = GameValue.for_league(POINTS, [])
    assert value.unit == "points"
    # 20 + 10 + 2 * 5 = 40; 29 points is under the bonus line, 31 reaches it (+3).
    assert value.of({Stat.POINTS: 20, Stat.REBOUNDS: 10, Stat.ASSISTS: 5}) == 40.0
    assert value.of({Stat.POINTS: 29}) == 29.0
    assert value.of({Stat.POINTS: 31}) == 34.0


def test_a_categories_game_is_its_categories_in_spreads_with_turnovers_against() -> None:
    value = GameValue.for_league(CATEGORIES, LINES)
    pts, to, fg = value.baselines
    assert pts.mean == 25.0
    assert pts.sd == pytest.approx(math.sqrt(500 / 3))
    assert to.mean == 2.5
    assert to.sd == pytest.approx(math.sqrt(5 / 3))
    assert fg.ratio == 0.5
    assert fg.mean == pytest.approx(0.0)
    assert fg.sd == pytest.approx(math.sqrt(14 / 3))
    assert value.unit == "category index"
    # The best game: +15 points is 1.1619 spreads; 4 turnovers is 1.5 over / 1.29099 = 1.1619
    # against; 8-of-12 is +2 makes over 50% / 2.1602 = 0.9258. Sum = 0.9258.
    assert value.of(LINES[3]) == pytest.approx(0.92582, abs=1e-4)
    # The worst: -15 points (-1.1619), 1 turnover (+1.1619 for turnovers), -1 make (0 + ... ):
    # 4-of-8 is exactly 50%, so 0. Sum = 0.
    assert value.of(LINES[0]) == pytest.approx(0.0, abs=1e-9)
    # A game of nothing (no stats) is -1.1619 on points, +1.9365 on turnovers (2.5 / 1.29099),
    # 0 on shooting.
    assert value.of({}) == pytest.approx(-25 / math.sqrt(500 / 3) + 2.5 / math.sqrt(5 / 3))


def test_a_category_with_no_spread_counts_nothing() -> None:
    same = [{Stat.POINTS: 10.0, Stat.TURNOVERS: 1.0}] * 3
    value = GameValue.for_league(CATEGORIES, same)
    assert value.of({Stat.POINTS: 99.0, Stat.TURNOVERS: 0.0}) == 0.0
    assert GameValue.for_league(CATEGORIES, []).of({Stat.POINTS: 99.0}) == 0.0
    assert category_baselines([], CATEGORIES.categories)[0].sd == 0.0


stat_lines = st.fixed_dictionaries(
    {
        Stat.POINTS: st.floats(0, 60),
        Stat.TURNOVERS: st.floats(0, 10),
        Stat.FIELD_GOALS_MADE: st.floats(0, 25),
        Stat.FIELD_GOALS_ATTEMPTED: st.floats(0, 40),
    }
)


@given(a=stat_lines, b=stat_lines)
@settings(max_examples=100)
def test_a_categories_value_is_affine_so_a_projected_line_is_the_expected_game(
    a: StatLine, b: StatLine
) -> None:
    value = GameValue.for_league(CATEGORIES, LINES)
    midpoint = {stat: (a[stat] + b[stat]) / 2 for stat in a}
    assert value.of(midpoint) == pytest.approx((value.of(a) + value.of(b)) / 2, abs=1e-6)


# ------------------------------------------------------------------- what was expected


def test_expected_game_for_points_blends_the_projection_with_his_own_games() -> None:
    value = GameValue.for_league(POINTS, [])
    projection: StatLine = {Stat.POINTS: 20, Stat.REBOUNDS: 10}  # 30 points
    fresh = expected_game(projection, value, history=[], prior_cv=0.3, prior_sd=3.0)
    assert fresh is not None
    assert fresh.mean == 30.0
    assert fresh.sd == pytest.approx(9.0)
    assert fresh.play_probability == 1.0
    # Two games of 20: the projection counts as 10 games, so (10 * 30 + 40) / 12.
    blended = expected_game(projection, value, history=[20, 20], prior_cv=0.3, prior_sd=3.0)
    assert blended is not None
    assert blended.mean == pytest.approx(340 / 12)
    assert expected_game(None, value, history=[1], prior_cv=0.3, prior_sd=3.0) is None
    sits = expected_game({}, value, history=[1], prior_cv=0.3, prior_sd=3.0)
    assert sits == GameDistribution(0.0, 0.0, 0.0)  # an empty row: not expected to play


def test_expected_game_for_categories_uses_the_index_and_an_absolute_spread() -> None:
    value = GameValue.for_league(CATEGORIES, LINES)
    projection = LINES[3]
    nothing = expected_game(projection, value, history=[], prior_cv=0.3, prior_sd=3.0)
    assert nothing is not None
    assert nothing.mean == pytest.approx(value.of(projection))
    assert nothing.sd == 3.0  # no games of his own: the league's spread
    # Two games, spread 1.0 around 1.0 and 3.0 (sample sd sqrt(2)): (2 * 2 + 5 * 9) / 7.
    some = expected_game(projection, value, history=[1.0, 3.0], prior_cv=0.3, prior_sd=3.0)
    assert some is not None
    assert some.sd == pytest.approx(math.sqrt((2 * 2.0 + 5.0 * 9.0) / 7.0))


# ------------------------------------------------------------------- who did what


def test_standouts_are_the_best_played_games_ties_by_name() -> None:
    perfs = [perf("Cy", 40), perf("Ab", 40), perf("Bo", 55), perf("Di", 0, played=False)]
    assert [p.name for p in standouts(perfs, 3)] == ["Bo", "Ab", "Cy"]
    assert [p.name for p in standouts(perfs, 1)] == ["Bo"]
    assert standouts([perf("Di", 0, played=False)], 5) == []


@given(values=st.lists(st.floats(-20, 90), max_size=30), n=st.integers(0, 10))
def test_standouts_are_sorted_a_subset_and_at_most_n(values: list[float], n: int) -> None:
    perfs = [perf(f"P{i}", v) for i, v in enumerate(values)]
    top = standouts(perfs, n)
    assert len(top) <= n
    assert [p.value for p in top] == sorted((p.value for p in top), reverse=True)
    assert {p.player_id for p in top} <= {p.player_id for p in perfs}
    if top:  # nobody left out beat the lowest one in
        rest = [p.value for p in perfs if p not in top]
        assert all(v <= top[-1].value for v in rest)


def test_the_good_game_line_is_a_quantile_of_the_seasons_games() -> None:
    assert good_game_line([float(i) for i in range(1, 30)]) is None  # 29 games: too few
    values = [float(i) for i in range(1, 41)]  # 40 games, 1..40
    # Position 0.75 * 39 = 29.25: between the 30th value (30) and the 31st (31).
    assert good_game_line(values) == pytest.approx(30.25)
    assert good_game_line(values, 1.0) == 40.0
    assert good_game_line(values, 0.0) == 1.0


@given(values=st.lists(st.floats(-50, 150), min_size=30, max_size=80), q=st.floats(0, 1))
def test_the_good_game_line_lies_within_the_games(values: list[float], q: float) -> None:
    line = good_game_line(values, q)
    assert line is not None
    assert min(values) - 1e-9 <= line <= max(values) + 1e-9


def test_a_gap_and_z_only_exist_for_a_player_who_played_as_expected_to() -> None:
    beat = perf("A", 40, expected=(30.0, 5.0))
    assert (beat.gap, beat.z) == (10.0, 2.0)
    assert perf("B", 40, expected=None).gap is None  # nothing to compare against
    assert perf("C", 40, expected=(0.0, 0.0), plays=0.0).gap is None  # not expected to play
    assert perf("D", 0, played=False, expected=(30.0, 5.0)).gap is None  # didn't play
    assert perf("E", 40, expected=(30.0, 0.0)).z is None  # no spread to measure by


def test_surprises_need_a_big_enough_miss_and_a_roster_for_a_bad_one() -> None:
    perfs = [
        perf("Beat", 45, expected=(30.0, 5.0)),  # z +3
        perf("Close", 33, expected=(30.0, 5.0)),  # z +0.6: not a surprise
        perf("Bust", 10, expected=(30.0, 5.0)),  # z -4
        perf("Slight", 25, expected=(30.0, 5.0)),  # z -1: exactly the line
        perf("Free Good", 50, expected=(30.0, 5.0), owner=None),  # z +4, on no roster
        perf("Free Meh", 36, expected=(30.0, 5.0), owner=None),  # z +1.2, but not a good game
        perf("Free Bust", 5, expected=(30.0, 5.0), owner=None),  # a miss nobody owns
    ]
    beat, missed = surprises(perfs, 5, good_line=40.0)
    assert [p.name for p in beat] == ["Free Good", "Beat"]  # a free agent counts if good
    assert [p.name for p in missed] == ["Bust", "Slight"]
    # Without a line, free agents don't count at all; on his own team, none are there anyway.
    beat, _ = surprises(perfs, 5, good_line=None)
    assert [p.name for p in beat] == ["Beat"]
    beat, missed = surprises(perfs, 1, min_z=3.0, good_line=40.0, rostered_only=True)
    assert [p.name for p in beat] == ["Beat"]
    assert [p.name for p in missed] == ["Bust"]


@given(
    z=st.lists(st.floats(-6, 6), max_size=20),
    n=st.integers(0, 5),
    min_z=st.floats(0.1, 3.0),
)
def test_surprises_are_far_enough_off_and_sorted(z: list[float], n: int, min_z: float) -> None:
    perfs = [perf(f"P{i}", 30 + 5 * v, expected=(30.0, 5.0)) for i, v in enumerate(z)]
    beat, missed = surprises(perfs, n, min_z=min_z)
    assert all((p.z or 0) >= min_z - 1e-9 for p in beat)
    assert all((p.z or 0) <= -min_z + 1e-9 for p in missed)
    assert [p.z for p in beat] == sorted((p.z for p in beat), reverse=True)  # type: ignore[type-var]
    assert len(beat) <= n
    assert len(missed) <= n


def test_expected_but_sat_is_a_rostered_player_who_should_have_played() -> None:
    perfs = [
        perf("Sat", 0, played=False, expected=(25.0, 5.0)),
        perf("Ruled Out", 0, played=False, expected=(0.0, 0.0), plays=0.0),
        perf("Unrostered", 0, played=False, expected=(25.0, 5.0), owner=None),
        perf("No Projection", 0, played=False, expected=None),
        perf("Played", 20, expected=(25.0, 5.0)),
    ]
    assert [p.name for p in expected_but_sat(perfs)] == ["Sat"]


def test_pickups_that_did_well_are_good_games_best_first() -> None:
    def pick(name: str, value: float, played: bool = True) -> Pickup:
        return Pickup(
            perf(name, value, played=played), team="4", added_day="2026-11-04", kind="waiver"
        )

    pickups = [pick("Mid", 31), pick("Big", 48), pick("Low", 12), pick("Out", 0, played=False)]
    assert [p.performance.name for p in pickups_that_did_well(pickups, 30.0, 5)] == ["Big", "Mid"]
    assert [p.performance.name for p in pickups_that_did_well(pickups, 30.0, 1)] == ["Big"]
    assert pickups_that_did_well(pickups, None, 5) == []  # no line yet: nothing to call good
    assert [p.performance.name for p in pickups_that_did_well(pickups, 31.0, 5)] == ["Big", "Mid"]


# ------------------------------------------------------------------- a team's day


def test_a_lineups_total_counts_starters_only() -> None:
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    values = {a: 30.5, b: 22.25, c: 18.0, d: 99.0}
    lineup = [(a, "PG"), (b, "UTIL"), (c, "BENCH"), (d, "IR"), (uuid4(), "C")]  # last: no game
    assert lineup_total(lineup, values) == 52.75
    assert lineup_total([], values) == 0.0


@given(
    starters=st.lists(st.floats(-10, 80), max_size=10),
    benched=st.lists(st.floats(-10, 80), max_size=10),
)
def test_benching_more_players_never_changes_a_lineups_total(
    starters: list[float], benched: list[float]
) -> None:
    ids = [uuid4() for _ in starters + benched]
    values = dict(zip(ids, starters + benched, strict=True))
    started = [(pid, "UTIL") for pid in ids[: len(starters)]]
    bench = [(pid, "BENCH") for pid in ids[len(starters) :]]
    assert lineup_total(started + bench, values) == lineup_total(started, values)


def test_category_totals_add_counting_stats_and_pool_the_ratios() -> None:
    totals = category_totals(LINES, CATEGORIES.categories)
    # Points 100, turnovers 10; 20 makes on 40 attempts pooled (not the average of four rates).
    assert totals == {"PTS": 100.0, "TO": 10.0, "FG%": 0.5}
    assert category_totals([], CATEGORIES.categories) == {"PTS": 0.0, "TO": 0.0, "FG%": 0.0}


def test_category_standing_counts_a_reversed_category_the_other_way() -> None:
    mine = {"PTS": 120.0, "TO": 14.0, "FG%": 0.48}
    theirs = {"PTS": 100.0, "TO": 10.0, "FG%": 0.48}
    standing = category_standing(mine, theirs, CATEGORIES.categories)
    assert standing.leading == ("PTS",)
    assert standing.trailing == ("TO",)  # more turnovers is worse
    assert standing.tied == ("FG%",)
    assert standing.record == "1-1-1"


@given(
    a=st.lists(st.floats(0, 200), min_size=3, max_size=3),
    b=st.lists(st.floats(0, 200), min_size=3, max_size=3),
)
def test_every_category_is_won_lost_or_tied_and_swapping_sides_swaps_it(
    a: list[float], b: list[float]
) -> None:
    names = [c.name for c in CATEGORIES.categories]
    mine, theirs = dict(zip(names, a, strict=True)), dict(zip(names, b, strict=True))
    one = category_standing(mine, theirs, CATEGORIES.categories)
    other = category_standing(theirs, mine, CATEGORIES.categories)
    assert len(one.leading) + len(one.trailing) + len(one.tied) == len(names)
    assert (one.leading, one.trailing, one.tied) == (other.trailing, other.leading, other.tied)


# ------------------------------------------------------------------- checking the totals


def test_totals_agree_within_the_tolerance_and_skip_what_the_platform_doesnt_report() -> None:
    ours = {"me_day": 142.5, "them_day": 118.0, "me_week": 512.0}
    reported = {"me_day": 142.5, "them_day": 120.0, "other": 5.0}
    agreement = compare_totals(ours, reported)
    assert (agreement.matched, agreement.total, agreement.gaps) == (1, 2, (-2.0,))
    assert compare_totals(ours, {"them_day": 118.4}).matched == 1  # 0.4 apart: agrees
    assert compare_totals(ours, {"them_day": 118.6}).matched == 0  # 0.6 apart: doesn't
    assert compare_totals({}, reported).total == 0


def test_a_ratio_category_uses_its_own_tight_tolerance() -> None:
    tolerance = {"PTS": 0.5, "FG%": 0.001}
    ours = {"PTS": 100.0, "FG%": 0.4812}
    assert compare_totals(ours, {"PTS": 100.4, "FG%": 0.4815}, tolerance=tolerance).total == 2
    assert compare_totals(ours, {"PTS": 100.4, "FG%": 0.4815}, tolerance=tolerance).matched == 2
    assert compare_totals(ours, {"PTS": 100.4, "FG%": 0.483}, tolerance=tolerance).matched == 1


@given(totals=st.dictionaries(st.text(min_size=1, max_size=4), st.floats(0, 1000), max_size=8))
def test_totals_always_agree_with_themselves(totals: dict[str, float]) -> None:
    agreement = compare_totals(totals, totals)
    assert agreement.matched == agreement.total == len(totals)
    assert agreement.gaps == ()
