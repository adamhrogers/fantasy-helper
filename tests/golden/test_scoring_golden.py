"""Golden scoring cases, kept forever (the M2 exit criterion): five real box scores scored by
hand in each points league, then by the engine.

Every league scores the same box scores (ADR 0022); only the rules differ, and each league's
rules come from its own recorded settings. Inputs are real: the box scores come from the
recorded ESPN game logs. Each expected value is worked out in the comment beside it. The
engine also matched the platforms on live data on 2026-11-06: ESPN's season totals for all 155
rostered players, and 405 of 417 Sleeper player-weeks (docs/spikes/m2-scoring-check.md).
"""

import pytest

from fantasy_helper.adapters.espn import mapper as espn
from fantasy_helper.adapters.sleeper import mapper as sleeper
from fantasy_helper.domain.league import ScoringRules
from fantasy_helper.domain.stats import StatLine
from fantasy_helper.engine.scoring import bonus_points, game_points, missing_stats
from tests.contract.conftest import recorded

# (player, ESPN game ID): the box score lines, as recorded.
WEMBANYAMA_52 = ("Victor Wembanyama", "401909933")  # 52 pts 24 reb 2 ast 2 stl 8 blk 1 to,
#                                                     16-28 FG, 19-23 FT, 1 3PM
JOHNSON_16_AST = ("Jalen Johnson", "401909856")  # 26 pts 9 reb 16 ast 2 stl 0 blk 2 to,
#                                                  8-19 FG, 8-10 FT, 2 3PM
EDWARDS_45 = ("Anthony Edwards", "401909091")  # 45 pts 10 reb 3 ast 2 stl 1 blk 4 to,
#                                                16-29 FG, 4-5 FT, 9 3PM
DONCIC_11_TO = ("Luka Doncic", "401909851")  # 23 pts 6 reb 7 ast 0 stl 1 blk 11 to,
#                                              8-21 FG, 5-7 FT, 2 3PM
FLAGG_17_MIN = ("Cooper Flagg", "401909844")  # 14 pts 5 reb 6 ast 3 stl 1 blk 3 to,
#                                               4-6 FG, 4-4 FT, 2 3PM


def box_score(player_and_game: tuple[str, str]) -> StatLine:
    name, game_id = player_and_game
    data = recorded("espn_public/game_logs.json")
    player_id = next(
        str(e["player"]["id"]) for e in data["players"] if e["player"]["fullName"] == name
    )
    (log,) = [
        g for g in espn.map_game_logs(data, 2027)
        if g.player.external_id == player_id and g.external_game_id == game_id
    ]  # fmt: skip
    return log.stats


@pytest.fixture(scope="module")
def espn_points() -> ScoringRules:
    # PTS +1, REB +1, AST +2, STL +4, BLK +4, TO -2, FGM +2, FGA -1, FTM +1, FTA -1, 3PM +1
    return espn.map_rules(recorded("espn_points/settings.json")).rules.scoring


@pytest.fixture(scope="module")
def sleeper_points() -> ScoringRules:
    # PTS +1, REB +1, AST +2, STL +3, BLK +3, TO -2, FGM +1, FGA -0.5, FTM +1, FTA -1,
    # 3PM +0.5. This league's settings also add 2 points at 40 points in a game and 2 more at
    # 50 (its own choice: other leagues set other bonuses, or none).
    return sleeper.map_rules(recorded("sleeper_points/league.json")).rules.scoring


ESPN_CASES = [
    # 52 + 24 + 2*2 + 2*4 + 8*4 - 1*2 + 16*2 - 28 + 19 - 23 + 1 = 119
    (WEMBANYAMA_52, 119.0),
    # 26 + 9 + 16*2 + 2*4 + 0 - 2*2 + 8*2 - 19 + 8 - 10 + 2 = 68
    (JOHNSON_16_AST, 68.0),
    # 45 + 10 + 3*2 + 2*4 + 1*4 - 4*2 + 16*2 - 29 + 4 - 5 + 9 = 76
    (EDWARDS_45, 76.0),
    # 23 + 6 + 7*2 + 0 + 1*4 - 11*2 + 8*2 - 21 + 5 - 7 + 2 = 20
    (DONCIC_11_TO, 20.0),
    # 14 + 5 + 6*2 + 3*4 + 1*4 - 3*2 + 4*2 - 6 + 4 - 4 + 2 = 45
    (FLAGG_17_MIN, 45.0),
]

SLEEPER_CASES = [
    # 52 + 24 + 2*2 + 2*3 + 8*3 - 1*2 + 16 - 28*0.5 + 19 - 23 + 1*0.5 = 106.5,
    # + 2 (40 points) + 2 (50 points) = 110.5
    (WEMBANYAMA_52, 110.5),
    # 26 + 9 + 16*2 + 2*3 + 0 - 2*2 + 8 - 19*0.5 + 8 - 10 + 2*0.5 = 66.5
    (JOHNSON_16_AST, 66.5),
    # 45 + 10 + 3*2 + 2*3 + 1*3 - 4*2 + 16 - 29*0.5 + 4 - 5 + 9*0.5 = 67, + 2 (40 points) = 69
    (EDWARDS_45, 69.0),
    # 23 + 6 + 7*2 + 0 + 1*3 - 11*2 + 8 - 21*0.5 + 5 - 7 + 2*0.5 = 20.5
    (DONCIC_11_TO, 20.5),
    # 14 + 5 + 6*2 + 3*3 + 1*3 - 3*2 + 4 - 6*0.5 + 4 - 4 + 2*0.5 = 39
    (FLAGG_17_MIN, 39.0),
]


@pytest.mark.parametrize(("game", "expected"), ESPN_CASES, ids=lambda x: str(x))
def test_espn_points_league(
    game: tuple[str, str], expected: float, espn_points: ScoringRules
) -> None:
    stats = box_score(game)
    assert missing_stats(stats, espn_points) == frozenset()
    assert game_points(stats, espn_points) == expected


@pytest.mark.parametrize(("game", "expected"), SLEEPER_CASES, ids=lambda x: str(x))
def test_sleeper_points_league(
    game: tuple[str, str], expected: float, sleeper_points: ScoringRules
) -> None:
    stats = box_score(game)
    assert missing_stats(stats, sleeper_points) == frozenset()
    assert game_points(stats, sleeper_points) == expected


def test_threshold_bonuses_stack(sleeper_points: ScoringRules) -> None:
    """A game reaching two of a league's thresholds earns both (this league: 40 and 50)."""
    assert bonus_points(box_score(WEMBANYAMA_52), sleeper_points) == 4.0  # 40+ and 50+
    assert bonus_points(box_score(EDWARDS_45), sleeper_points) == 2.0  # 40+ only
    assert bonus_points(box_score(DONCIC_11_TO), sleeper_points) == 0.0


def test_a_game_sat_out_scores_zero(
    espn_points: ScoringRules, sleeper_points: ScoringRules
) -> None:
    assert game_points({}, espn_points) == 0.0
    assert game_points({}, sleeper_points) == 0.0
