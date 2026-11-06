from collections import Counter

from fantasy_helper.adapters.sleeper import mapper
from fantasy_helper.domain.league import LineupMode, ScoringBonus
from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.records import TransactionKind
from fantasy_helper.domain.stats import Stat
from tests.contract.conftest import recorded

LEAGUE = "sleeper_points"


def test_rules_bonuses_and_lock_in() -> None:
    record = mapper.map_rules(recorded(f"{LEAGUE}/league.json"))
    rules = record.rules
    assert record.unmapped_scoring == ()
    assert rules.scoring.weights == {
        Stat.POINTS: 1, Stat.REBOUNDS: 1, Stat.ASSISTS: 2, Stat.STEALS: 3, Stat.BLOCKS: 3,
        Stat.TURNOVERS: -2, Stat.FIELD_GOALS_MADE: 1, Stat.FIELD_GOALS_ATTEMPTED: -0.5,
        Stat.FREE_THROWS_MADE: 1, Stat.FREE_THROWS_ATTEMPTED: -1, Stat.THREES_MADE: 0.5,
    }  # fmt: skip
    assert set(rules.scoring.bonuses) == {
        ScoringBonus(stat=Stat.POINTS, threshold=40, points=2),
        ScoringBonus(stat=Stat.POINTS, threshold=50, points=2),
    }
    assert rules.lineup_mode is LineupMode.LOCK_IN  # Adam confirmed the league is lock-in
    assert rules.team_count == 10
    assert {s.slot: s.count for s in rules.roster_slots} == {
        "PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1,
        "UTIL": 3, "BENCH": 3, "IR": 2,
    }  # fmt: skip


def test_rosters_place_every_player_once() -> None:
    league = recorded(f"{LEAGUE}/league.json")
    rosters = recorded(f"{LEAGUE}/rosters.json")
    teams, entries = mapper.map_rosters(league, rosters, recorded(f"{LEAGUE}/users.json"))
    assert len(teams) == 10
    assert all(team.name.startswith("Team ") for team in teams)  # redacted fixture
    expected = sum(len(set(r.get("players") or []) - set(r.get("taxi") or [])) for r in rosters)
    assert len(entries) == expected
    per_team = Counter((e.team_external_id, e.player.external_id) for e in entries)
    assert max(per_team.values()) == 1
    assert all(e.player.source is IdSource.SLEEPER for e in entries)
    starting = sum(1 for p in league["roster_positions"] if p != "BN")
    for team in teams:
        starters = [
            e
            for e in entries
            if e.team_external_id == team.external_id and e.slot not in ("BENCH", "IR")
        ]
        assert len(starters) <= starting


def test_matchups_pair_up_each_week() -> None:
    for week in (1, 2, 3):
        matchups = mapper.map_matchups(week, recorded(f"{LEAGUE}/matchups_{week}.json"))
        assert len(matchups) == 5
        assert all(m.away is not None for m in matchups)
        assert all(m.period == week for m in matchups)
    week1 = mapper.map_matchups(1, recorded(f"{LEAGUE}/matchups_1.json"))
    assert any(m.home.player_points for m in week1)


def test_transactions() -> None:
    records = mapper.map_transactions(recorded(f"{LEAGUE}/transactions_3.json"))
    assert Counter(r.kind for r in records) == {
        TransactionKind.FREE_AGENT: 10,
        TransactionKind.WAIVER: 3,
        TransactionKind.COMMISSIONER: 1,
    }
    assert all(r.scoring_period == 3 for r in records)
    assert all(r.proposed_at is not None for r in records)
