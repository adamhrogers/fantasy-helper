"""Startable games, replacement level, win probability and streaming: hand-checked cases and
properties."""

from datetime import date
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fantasy_helper.domain.league import RosterSlot
from fantasy_helper.domain.player import Position
from fantasy_helper.engine.baselines import top_projected_free_agent
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.schedule import PlayerWeek, fill_day, project_week, starting_slots
from fantasy_helper.engine.streaming import plan_streams
from fantasy_helper.engine.valuation import replacement_levels, value_over_replacement
from fantasy_helper.engine.win_probability import win_probability

C, PG, SF = (Position.C,), (Position.PG,), (Position.SF,)
MON, TUE, WED = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)
SLOTS = (RosterSlot.named("C", 1), RosterSlot.named("UTIL", 1), RosterSlot.named("BENCH", 3))


def week(positions: tuple[Position, ...], *games: tuple[date, float]) -> PlayerWeek:
    return PlayerWeek(
        uuid4(), positions, tuple((d, GameDistribution(m, 0.3 * m)) for d, m in games)
    )


def test_the_most_specific_slot_fills_first() -> None:
    assert starting_slots(SLOTS)[0] == C  # C before UTIL; the bench never starts
    center, guard = uuid4(), uuid4()
    # The center has more points: he takes C, leaving UTIL for the guard.
    assert fill_day([(center, C, 40.0), (guard, PG, 30.0)], starting_slots(SLOTS)) == {
        center, guard,
    }  # fmt: skip
    # Two guards: only one starts (UTIL); C stays empty.
    g1, g2 = uuid4(), uuid4()
    assert fill_day([(g1, PG, 30.0), (g2, PG, 20.0)], starting_slots(SLOTS)) == {g1}


def test_a_hand_checked_week() -> None:
    big = week(C, (MON, 40.0), (WED, 40.0))
    guard = week(PG, (MON, 30.0), (TUE, 30.0))
    wing = week(SF, (MON, 25.0))  # Monday: UTIL goes to the guard, so the wing sits
    projection = project_week([big, guard, wing], SLOTS)
    assert projection.games == 5
    assert projection.startable == 4
    assert projection.points == pytest.approx(40 + 40 + 30 + 30)
    assert projection.started_games(wing.player_id) == 0
    # variance: each started game's sd² (sd = 0.3 * mean), all played for certain
    assert projection.variance == pytest.approx(2 * 12.0**2 + 2 * 9.0**2)


rosters = st.lists(
    st.builds(
        lambda pos, games: week(pos, *games),
        st.sampled_from([C, PG, SF]),
        st.lists(
            st.tuples(st.sampled_from([MON, TUE, WED]), st.floats(1, 60)),
            max_size=3,
            unique_by=lambda g: g[0],
        ),
    ),
    max_size=8,
)


@given(rosters)
def test_startable_games_never_exceed_games_or_slots(players: list[PlayerWeek]) -> None:
    projection = project_week(players, SLOTS)
    assert projection.startable <= projection.games
    assert projection.startable <= 2 * 3  # two starting slots, three days
    assert projection.variance >= -1e-9


def test_replacement_level_and_value_over_it() -> None:
    free_agents = [(C, 18.0), (PG, 22.0), (SF, 15.0)]
    levels = replacement_levels(free_agents, SLOTS)
    assert levels == {"C": 18.0, "UTIL": 22.0}  # the bench isn't a starting slot
    # A center averaging 25: compared at UTIL, where the best free agent (22) could replace him.
    assert value_over_replacement(25.0, C, SLOTS, levels) == pytest.approx(3.0)


def test_win_probability() -> None:
    assert win_probability(100, 100, 100, 100) == pytest.approx(0.5)
    assert win_probability(110, 100, 100, 100) > 0.5
    assert win_probability(110, 0, 100, 0) == 1.0


@given(st.floats(0, 2000), st.floats(1, 5000), st.floats(0, 2000), st.floats(1, 5000))
def test_win_probabilities_are_symmetric(a: float, va: float, b: float, vb: float) -> None:
    assert win_probability(a, va, b, vb) + win_probability(b, vb, a, va) == pytest.approx(1.0)


def test_streaming_fills_an_empty_day() -> None:
    big = week(C, (MON, 40.0))
    guard = week(PG, (MON, 30.0))
    idle = week(SF)  # no games this week
    tuesday_wing = week(SF, (TUE, 26.0), (WED, 24.0))
    moves = plan_streams([big, guard, idle], [tuesday_wing], SLOTS, adds_left=2)
    assert len(moves) == 1  # one good free agent, so one move even with 2 adds left
    (move,) = moves
    assert (move.add, move.drop) == (tuesday_wing.player_id, idle.player_id)
    assert move.gain == pytest.approx(50.0)
    assert move.games_added == 2
    assert plan_streams([big, guard, idle], [tuesday_wing], SLOTS, adds_left=0) == []
    keep: frozenset[UUID] = frozenset({idle.player_id, big.player_id, guard.player_id})
    assert plan_streams([big, guard, idle], [tuesday_wing], SLOTS, adds_left=2, keep=keep) == []


def test_the_streaming_baseline_ignores_the_schedule() -> None:
    one_big_game = week(C, (MON, 35.0))
    three_ok_games = week(PG, (MON, 25.0), (TUE, 25.0), (WED, 25.0))
    assert top_projected_free_agent([one_big_game, three_ok_games]) == one_big_game.player_id
    assert top_projected_free_agent([]) is None
