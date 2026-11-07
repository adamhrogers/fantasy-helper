"""Checking our scores against the platforms' numbers: the agreement math and ESPN's totals."""

from fantasy_helper.adapters.espn.mapper import map_season_points
from fantasy_helper.engine.reconcile import Agreement, one_game_a_week, season_totals
from fantasy_helper.pipeline.reconcile import ONE_GAME_A_WEEK_MIN, SEASON_TOTALS_MIN


def test_season_totals_compare_only_players_in_both() -> None:
    ours = {"1": 300.0, "2": 250.5, "3": 99.0}
    reported = {"1": 300.0, "2": 252.5, "4": 10.0}
    agreement = season_totals(ours, reported)
    assert (agreement.matched, agreement.total, agreement.gaps) == (1, 2, (-2.0,))
    assert agreement.rate == 0.5


def test_one_game_a_week_matches_any_of_his_games() -> None:
    reported = [("a", 45.5), ("b", 30.0), ("c", 12.0), ("d", 0.0)]
    games = {"a": [45.5, 32.0], "b": [33.0, 28.0], "c": [12.0], "d": []}  # d had no games
    agreement = one_game_a_week(reported, games)
    assert (agreement.matched, agreement.total) == (2, 3)
    assert agreement.gaps == (-2.0,)  # nearest game was 28.0: 2 under Sleeper's 30.0


def test_agreements_add_up_and_empty_is_no_verdict() -> None:
    assert (Agreement(2, 3) + Agreement(4, 4)).rate == 6 / 7
    assert Agreement(0, 0).rate is None


def test_thresholds_leave_room_only_for_feed_differences() -> None:
    assert SEASON_TOTALS_MIN == 0.98  # ESPN matched exactly on 2026-11-06
    assert ONE_GAME_A_WEEK_MIN == 0.90  # Sleeper's feed differs by one stat ~1% of games


def test_espns_season_totals_come_from_league_context() -> None:
    def player(pid: int, total: float | None, season: int = 2027) -> dict[str, object]:
        stat: dict[str, object] = {"statSourceId": 0, "statSplitTypeId": 0, "seasonId": season}
        if total is not None:
            stat["appliedTotal"] = total
        return {"playerPoolEntry": {"player": {"id": pid, "stats": [stat]}}}

    data = {"teams": [{"roster": {"entries": [
        player(1, 312.5), player(2, None), player(3, 100.0, season=2026),
    ]}}]}  # fmt: skip
    assert map_season_points(data, 2027) == {"1": 312.5}
