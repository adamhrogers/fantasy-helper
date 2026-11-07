from collections import Counter
from datetime import UTC, date, datetime

import pytest

from fantasy_helper.adapters.espn import mapper
from fantasy_helper.domain.league import LeagueRules, LineupMode, ScoringType
from fantasy_helper.domain.player import NBA_TEAMS, IdSource, Position
from fantasy_helper.domain.projections import InjuryStatus
from fantasy_helper.domain.records import ItemAction, TransactionKind, TransactionStatus
from fantasy_helper.domain.stats import Stat
from tests.contract.conftest import recorded


def slot_counts(rules: LeagueRules) -> dict[str, int]:
    return {slot.slot: slot.count for slot in rules.roster_slots}


class TestPointsLeague:
    def test_other_leagues_scoring_maps_onto_the_same_box_score(self) -> None:
        """Settings this league doesn't use but others might: missed shots fold into attempts
        and makes; technical fouls are a box score stat; anything else is reported."""
        settings = recorded("espn_points/settings.json")
        items = settings["settings"]["scoringSettings"]["scoringItems"]
        items += [
            {"statId": 23, "points": -1.0},  # field goals missed
            {"statId": 10, "points": -2.0},  # technical fouls
            {"statId": 43, "points": 1.0},  # team win: not a player box score stat
        ]
        record = mapper.map_rules(settings)
        weights = record.rules.scoring.weights
        assert weights[Stat.FIELD_GOALS_ATTEMPTED] == -2.0  # -1 (FGA) - 1 (missed)
        assert weights[Stat.FIELD_GOALS_MADE] == 3.0  # +2 (FGM) + 1 (missed)
        assert weights[Stat.TECHNICAL_FOULS] == -2.0
        assert record.unmapped_scoring == ("statId 43 (+1)",)

    def test_a_past_days_lineup(self) -> None:
        """Recorded `--only espn_points/lineups.json`: view mRoster for scoring period 16
        (Wednesday 2026-11-04), every team's slots that day."""
        data = recorded("espn_points/lineups.json")
        assert data["scoringPeriodId"] == 16
        entries = mapper.map_lineup(data)
        assert len(entries) == 155
        starters = Counter(e.team_external_id for e in entries if e.slot not in ("BENCH", "IR"))
        assert len(starters) == 10
        assert set(starters.values()) == {10}  # 10 starting slots, all filled that day
        assert mapper.scoring_period_of(date(2026, 11, 4), date(2026, 10, 20)) == 16

    def test_rules_match_the_league_settings(self) -> None:
        record = mapper.map_rules(recorded("espn_points/settings.json"))
        rules = record.rules
        assert record.unmapped_scoring == ()
        assert rules.scoring.scoring_type is ScoringType.POINTS
        assert rules.scoring.weights == {
            Stat.POINTS: 1, Stat.BLOCKS: 4, Stat.STEALS: 4, Stat.ASSISTS: 2, Stat.REBOUNDS: 1,
            Stat.TURNOVERS: -2, Stat.FIELD_GOALS_MADE: 2, Stat.FIELD_GOALS_ATTEMPTED: -1,
            Stat.FREE_THROWS_MADE: 1, Stat.FREE_THROWS_ATTEMPTED: -1, Stat.THREES_MADE: 1,
        }  # fmt: skip
        assert rules.team_count == 10
        assert rules.lineup_mode is LineupMode.DAILY
        assert slot_counts(rules) == {
            "PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1,
            "UTIL": 3, "BENCH": 4, "IR": 2,
        }  # fmt: skip
        limits = rules.acquisition_limits
        assert limits.per_matchup_per_day == pytest.approx(4 / 7)  # 4 adds per 7-day matchup
        assert limits.per_season is None
        assert limits.per_matchup is None

    def test_status(self) -> None:
        status = mapper.league_status(recorded("espn_points/settings.json"))
        assert (status.latest_scoring_period, status.current_matchup_period) == (18, 3)
        assert (status.first_scoring_period, status.final_scoring_period) == (1, 153)

    def test_rosters(self) -> None:
        teams, entries = mapper.map_rosters(recorded("espn_points/teams_rosters.json"))
        assert len(teams) == 10
        assert all(team.name.startswith("Team ") for team in teams)  # redacted fixture
        assert len(entries) == 155
        assert Counter(e.slot for e in entries)["BENCH"] == 48
        assert Counter(e.acquisition_type for e in entries) == {"DRAFT": 96, "ADD": 36, "TRADE": 23}
        assert all(e.player.source is IdSource.ESPN for e in entries)
        assert all(e.acquired_at is not None for e in entries)

    def test_matchups(self) -> None:
        matchups = mapper.map_matchups(recorded("espn_points/matchups.json"))
        assert len(matchups) == 90
        assert Counter(m.winner for m in matchups) == {None: 80, "away": 6, "home": 4}
        first = next(m for m in matchups if m.period == 1)
        assert first.away is not None
        assert first.home.points_by_period  # daily points are present

    def test_transactions_and_trades(self) -> None:
        responses = list(recorded("espn_points/transactions.json")["by_scoring_period"].values())
        records, unresolved = mapper.map_transactions(responses)
        assert len(records) == 118
        free_agents = [r for r in records if r.kind is TransactionKind.FREE_AGENT]
        assert len(free_agents) == 61
        assert all(r.status is TransactionStatus.EXECUTED for r in free_agents)
        add = next(i for r in free_agents for i in r.items if i.action is ItemAction.ADD)
        assert add.from_team is None  # from free agency
        assert add.to_team is not None

        trades = [r for r in records if r.is_completed_trade]
        assert len(trades) == 10
        # ESPN shows proposals only to the teams involved, so only trades involving the user's
        # team (13) resolve to players here. Trades between other teams come from the league
        # activity feed instead (M1 spike).
        resolved = [t for t in trades if t.items]
        assert (len(resolved), unresolved) == (6, 4)
        assert all(any("13" in (i.from_team, i.to_team) for i in t.items) for t in resolved)
        for trade in resolved:
            assert {i.action for i in trade.items} == {ItemAction.TRADE}
            assert all(i.from_team and i.to_team and i.from_team != i.to_team for i in trade.items)
        statuses = Counter(r.status for r in records if r.kind is TransactionKind.TRADE)
        assert statuses[TransactionStatus.DECLINED] == 8


class TestCategoriesLeague:
    def test_nine_categories(self) -> None:
        record = mapper.map_rules(recorded("espn_categories/settings.json"))
        scoring = record.rules.scoring
        assert scoring.scoring_type is ScoringType.CATEGORIES
        by_name = {c.name: c for c in scoring.categories}
        assert set(by_name) == {"PTS", "BLK", "STL", "AST", "REB", "TO", "3PM", "FG%", "FT%"}
        assert not by_name["TO"].higher_is_better
        assert by_name["FG%"].ratio_of == (Stat.FIELD_GOALS_MADE, Stat.FIELD_GOALS_ATTEMPTED)
        assert by_name["PTS"].stat is Stat.POINTS
        assert slot_counts(record.rules)["BENCH"] == 3
        assert slot_counts(record.rules)["IR"] == 3
        assert record.rules.acquisition_limits.per_matchup_per_day == 1.0

    def test_rosters_and_transactions_map(self) -> None:
        teams, entries = mapper.map_rosters(recorded("espn_categories/teams_rosters.json"))
        assert len(teams) == 10
        assert entries
        responses = list(
            recorded("espn_categories/transactions.json")["by_scoring_period"].values()
        )
        records, _ = mapper.map_transactions(responses)
        assert records


class TestPublicPlayerData:
    def test_players_use_standard_team_codes_and_positions(self) -> None:
        players = mapper.map_players(recorded("espn_public/players_projections.json"))
        assert len(players) == 40
        assert all(p.nba_team in NBA_TEAMS or p.nba_team is None for p in players)
        wemby = next(p for p in players if p.full_name == "Victor Wembanyama")
        assert wemby.nba_team == "SAS"
        assert wemby.positions == (Position.C,)
        assert wemby.key.source is IdSource.ESPN

    def test_injuries(self) -> None:
        injuries = mapper.map_injuries(recorded("espn_public/players_projections.json"))
        assert Counter(i.status for i in injuries) == {
            InjuryStatus.ACTIVE: 35,
            InjuryStatus.DAY_TO_DAY: 3,
            InjuryStatus.OUT: 2,
        }

    def test_season_projections_are_per_game_with_games(self) -> None:
        data = recorded("espn_public/players_projections.json")
        projections = {p.player.external_id: p for p in mapper.map_projections(data, 2027)}
        wemby_id = next(
            str(e["player"]["id"]) for e in data["players"]
            if e["player"]["fullName"] == "Victor Wembanyama"
        )  # fmt: skip
        wemby = projections[wemby_id]
        assert wemby.per_game[Stat.POINTS] == pytest.approx(26.85, abs=0.01)
        assert wemby.games == 67
        assert wemby.game_date is None
        assert len(projections) >= 35

    def test_schedule_is_deduplicated_with_utc_tip_times(self) -> None:
        games = mapper.map_schedule(recorded("espn_public/pro_schedule.json"))
        assert len({g.external_id for g in games}) == len(games)
        assert all(g.home_team in NBA_TEAMS and g.away_team in NBA_TEAMS for g in games)
        first = next(g for g in games if g.external_id == "401909839")
        assert first.tip_utc == datetime(2026, 10, 22, 0, 0, tzinfo=UTC)  # 8 p.m. ET, Oct 21
        assert first.scoring_period == 2

    def test_game_logs_are_one_row_per_player_and_game(self) -> None:
        """Recorded 2026-11-07 (`--only espn_public/game_logs.json`): the top 40 players'
        last 12 team games, which covers opening night (period 1) through period 17."""
        data = recorded("espn_public/game_logs.json")
        logs = mapper.map_game_logs(data, 2027)
        assert len(logs) == 333
        assert Counter(g.played for g in logs) == {True: 306, False: 27}
        assert len({g.player.external_id for g in logs}) == 40
        assert len({g.external_game_id for g in logs}) == 120
        assert len({(g.player.external_id, g.external_game_id) for g in logs}) == len(logs)
        assert all(g.team in NBA_TEAMS for g in logs)
        assert min(g.scoring_period for g in logs) == 1
        # A game a player sat out is a row with no stats; a played game balances its points.
        assert all(not g.stats for g in logs if not g.played)
        for g in (g for g in logs if g.played):
            s = g.stats
            made = 2 * s[Stat.FIELD_GOALS_MADE] + s[Stat.THREES_MADE] + s[Stat.FREE_THROWS_MADE]
            assert s[Stat.POINTS] == made
        # The depth counts back across seasons; last season's entries are kept apart.
        assert len(mapper.map_game_logs(data, 2026)) == 147

    def test_a_fifty_point_box_score(self) -> None:
        data = recorded("espn_public/game_logs.json")
        wemby_id = next(
            str(e["player"]["id"]) for e in data["players"]
            if e["player"]["fullName"] == "Victor Wembanyama"
        )  # fmt: skip
        games = {g.external_game_id: g for g in mapper.map_game_logs(data, 2027)
                 if g.player.external_id == wemby_id}  # fmt: skip
        assert len(games) == 8
        game = games["401909933"]
        assert (game.scoring_period, game.team, game.played) == (16, "SAS", True)
        assert {k: game.stats[k] for k in (Stat.POINTS, Stat.REBOUNDS, Stat.BLOCKS)} == {
            Stat.POINTS: 52.0, Stat.REBOUNDS: 24.0, Stat.BLOCKS: 8.0,
        }  # fmt: skip

    def test_roster_players_carry_identity_fields(self) -> None:
        players = mapper.map_roster_players(recorded("espn_points/teams_rosters.json"))
        assert len(players) == 155
        assert all(p.full_name for p in players)


def test_birth_dates_from_the_athletes_list() -> None:
    data = {"items": [
        {"id": "4431679", "dateOfBirth": "1999-09-19T07:00Z"},
        {"id": "1", "dateOfBirth": None},
    ]}  # fmt: skip
    assert mapper.map_birth_dates(data) == {"4431679": date(1999, 9, 19)}
    players = mapper.map_players(
        {"players": [{"player": {"id": 4431679, "fullName": "X Y", "proTeamId": 24}}]},
        mapper.map_birth_dates(data),
    )
    assert players[0].birth_date == date(1999, 9, 19)
