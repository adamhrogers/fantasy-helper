from collections import Counter

import pytest

from fantasy_helper.adapters.espn import mapper
from fantasy_helper.domain.league import LeagueRules, LineupMode, ScoringType
from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.records import ItemAction, TransactionKind, TransactionStatus
from fantasy_helper.domain.stats import Stat
from tests.contract.conftest import recorded


def slot_counts(rules: LeagueRules) -> dict[str, int]:
    return {slot.slot: slot.count for slot in rules.roster_slots}


class TestPointsLeague:
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
