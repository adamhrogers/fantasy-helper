"""Last night's facts and cards for one league, from a small hand-built world in Postgres.

The world is generic (players A to J, teams 1 to 3) and every number below is worked out by hand
in the comments. Week 1 runs Monday 2026-11-02 to Sunday 2026-11-08 and the report is built on
the morning of Thursday 2026-11-05, so "last night" is Wednesday 2026-11-04.

Points (PTS + REB + 2 * AST) per game, with the lineup each had:

    day  | me (team 1): A, B start; C benched | opponent (team 2): D, E start; F benched
    Mon  | A 29, B 15; C 50                   | D 30, E 14; F 11
    Tue  | A 38; B no game                    | D 45, E 22
    Wed  | A 54, B 14; C 40                   | D 22, E sat out (a box score row, no minutes); F 20
         = me 68, opponent 22 last night; the week so far 150 to 133.

Also on Wednesday: team 3's G scored 66, H (picked up Tuesday) 41, and nobody's I 34. J, on team
3, scored 31.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from fantasy_helper.domain.league import (
    CategoryRule,
    League,
    LeagueRules,
    LineupMode,
    Platform,
    RosterSlot,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.records import (
    ItemAction,
    MatchupRecord,
    MatchupSide,
    PlayerKey,
    ProjectionRecord,
    RosterEntryRecord,
    RulesRecord,
    ScheduledGameRecord,
    TeamRecord,
    TransactionItem,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)
from fantasy_helper.domain.reports import CardKind
from fantasy_helper.domain.stats import Stat
from fantasy_helper.domain.users import Role, User
from fantasy_helper.engine.distributions import blended_mean
from fantasy_helper.engine.recap import good_game_line
from fantasy_helper.facts.daily import DailySheet, MatchupMode, load_daily
from fantasy_helper.pipeline.ingest import _finished_days
from fantasy_helper.reports.recap import recap_cards
from fantasy_helper.storage.repositories import league_data, leagues, nba, users
from fantasy_helper.storage.tables import (
    InjuryObservationRow,
    PlayerExternalIdRow,
    PlayerGameStatRow,
    PlayerRow,
)

pytestmark = pytest.mark.db

# 06:20 Eastern on Thursday 2026-11-05 (daylight time ended on 2026-11-01: UTC-5).
NOW = datetime(2026, 11, 5, 11, 20, tzinfo=UTC)
MON, TUE, WED = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)

# (pts, reb, ast, tov, fgm, fga) per player per day; None is a game he sat out.
LINES: dict[str, dict[date, tuple[float, ...] | None]] = {
    "A": {MON: (20, 5, 2, 1, 8, 16), TUE: (22, 8, 4, 2, 9, 17), WED: (31, 9, 7, 3, 11, 20)},
    "B": {MON: (10, 3, 1, 0, 4, 8), WED: (8, 2, 2, 1, 3, 9)},
    "C": {MON: (30, 10, 5, 2, 12, 22), WED: (25, 5, 5, 2, 9, 18)},
    "D": {MON: (18, 6, 3, 1, 7, 15), TUE: (25, 10, 5, 3, 10, 18), WED: (15, 5, 1, 2, 6, 14)},
    "E": {MON: (12, 2, 0, 0, 5, 10), TUE: (9, 9, 2, 1, 4, 9), WED: None},
    "F": {MON: (8, 1, 1, 0, 3, 7), WED: (12, 4, 2, 1, 5, 10)},
    "G": {WED: (42, 12, 6, 4, 15, 27)},
    "H": {WED: (28, 7, 3, 2, 10, 19)},
    "I": {WED: (24, 6, 2, 1, 9, 16)},
    "J": {WED: (20, 5, 3, 1, 8, 15)},
}
# Wednesday's lineups: (team, slot). H sits on team 3's bench; I is on no roster.
TEAMS = {
    "A": ("1", "UTIL"), "B": ("1", "UTIL"), "C": ("1", "BENCH"),
    "D": ("2", "UTIL"), "E": ("2", "UTIL"), "F": ("2", "BENCH"),
    "G": ("3", "UTIL"), "H": ("3", "BENCH"), "J": ("3", "UTIL"),
}  # fmt: skip
# What Sleeper projected for Wednesday's game (pts, reb, ast), pulled before the tip.
PROJECTED = {
    "A": (20, 8, 4),  # 36 points; with his games (29, 38): (10 * 36 + 67) / 12 = 35.58
    "B": (20, 6, 3),  # 32; with (15): (10 * 32 + 15) / 11 = 30.45
    "D": (20, 7, 3),  # 33; with (30, 45): (10 * 33 + 75) / 12 = 33.75
    "E": (15, 5, 2),  # 25: expected to play, and didn't
    "G": (25, 8, 3),  # 39
    "H": (10, 3, 1),  # 15
    "I": (10, 3, 1),  # 15
}

POINTS_RULES = ScoringRules(
    scoring_type=ScoringType.POINTS,
    weights={Stat.POINTS: 1.0, Stat.REBOUNDS: 1.0, Stat.ASSISTS: 2.0},
)
CATEGORY_RULES = ScoringRules(
    scoring_type=ScoringType.CATEGORIES,
    categories=(
        CategoryRule(name="PTS", stat=Stat.POINTS),
        CategoryRule(name="REB", stat=Stat.REBOUNDS),
        CategoryRule(name="AST", stat=Stat.ASSISTS),
        CategoryRule(name="TO", stat=Stat.TURNOVERS, higher_is_better=False),
        CategoryRule(name="FG%", ratio_of=(Stat.FIELD_GOALS_MADE, Stat.FIELD_GOALS_ATTEMPTED)),
    ),
)


@dataclass
class World:
    league: League
    players: dict[str, UUID]
    game_ids: dict[date, UUID]
    pickup_id: str  # the platform's own id for the player picked up on Tuesday


@pytest.fixture
def session(engine: Engine):  # type: ignore[no-untyped-def]
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def key(name: str) -> PlayerKey:
    return PlayerKey(source=IdSource.ESPN, external_id=name)


def build_world(
    session: Session,
    *,
    scoring: ScoringRules = POINTS_RULES,
    platform: Platform = Platform.ESPN,
    lock_in: bool = False,
    reported: dict[str, dict[int, float]] | None = None,
    lineups_on: tuple[date, ...] = (MON, TUE, WED),
) -> World:
    """The world in the module docstring. `reported` overrides what the platform says each team
    scored by scoring day (ESPN period 1 is Monday)."""
    owner = users.create(
        session,
        User(email=f"{uuid4().hex[:8]}@example.com", display_name="Owner",
             timezone="America/New_York", role=Role.OWNER),
    )  # fmt: skip
    league = League(
        user_id=owner.id, platform=platform, external_id="L1", season=2027, name="Test League",
        my_team_external_id="1",
    )  # fmt: skip
    leagues.upsert(session, league)
    rules = LeagueRules(
        team_count=3, scoring=scoring,
        lineup_mode=LineupMode.LOCK_IN if lock_in else LineupMode.DAILY,
        roster_slots=(RosterSlot.named("UTIL", 2), RosterSlot.named("BENCH", 2)),
    )  # fmt: skip
    league_data.save_rules(session, league.id, RulesRecord(rules=rules), NOW)
    league_data.upsert_teams(
        session, league.id,
        [TeamRecord(external_id="1", name="My Team"), TeamRecord(external_id="2", name="Rivals"),
         TeamRecord(external_id="3", name="Third Team")],
    )  # fmt: skip

    tips = {  # the Eastern evening of each day, in UTC
        MON: datetime(2026, 11, 3, 0, 30, tzinfo=UTC),
        TUE: datetime(2026, 11, 4, 0, 30, tzinfo=UTC),
        WED: datetime(2026, 11, 5, 0, 30, tzinfo=UTC),
    }
    nba.upsert_games(
        session, 2027,
        [ScheduledGameRecord(external_id=f"g{i}", scoring_period=i + 1, tip_utc=tip,
                             home_team="BOS", away_team="NYK")
         for i, tip in enumerate(tips.values())],
    )  # fmt: skip
    game_ids = {day: nba.game_index(session, 2027)[f"g{i}"][0] for i, day in enumerate(tips)}

    ids = {name: uuid4() for name in LINES}
    for name, pid in ids.items():
        session.add(PlayerRow(id=pid, full_name=f"Player {name}", nba_team="BOS",
                              positions=["PG"]))  # fmt: skip
    fillers = [uuid4() for _ in range(40)]  # Monday games worth 1..40: enough for a good-game line
    for i, pid in enumerate(fillers, start=1):
        session.add(PlayerRow(id=pid, full_name=f"Filler {i}", nba_team="BOS", positions=["C"]))
    session.flush()
    pickup_id = str(uuid4().int % 10**9)  # unique, so a test can build two worlds
    session.add(PlayerExternalIdRow(source=platform.value, external_id=pickup_id,
                                    player_id=ids["H"], match_method="primary"))  # fmt: skip
    for i, pid in enumerate(fillers, start=1):
        session.add(PlayerGameStatRow(
            player_id=pid, game_id=game_ids[MON], season=2027, game_date=MON,
            scoring_period=1, team="BOS", played=True, pts=float(i),
        ))  # fmt: skip
    for name, by_day in LINES.items():
        for day, line in by_day.items():
            stats = {}
            if line is not None:
                stats = dict(zip(("pts", "reb", "ast", "tov", "fgm", "fga"), line, strict=True))
            session.add(PlayerGameStatRow(
                player_id=ids[name], game_id=game_ids[day], season=2027, game_date=day,
                scoring_period=(day - MON).days + 1, team="BOS", played=line is not None, **stats,
            ))  # fmt: skip
    session.flush()

    for day in lineups_on:
        entries = [
            (RosterEntryRecord(team_external_id=team, player=key(name), slot=slot), ids[name])
            for name, (team, slot) in TEAMS.items()
            if not (name == "H" and day == MON)  # picked up on Tuesday
            and LINES[name].get(day, "absent") != "absent"
        ]
        league_data.replace_roster(session, league.id, day, entries)

    by_period = reported or {
        "1": {1: 44.0, 2: 38.0, 3: 68.0}, "2": {1: 44.0, 2: 67.0, 3: 22.0},
    }  # fmt: skip
    categories = {
        "1": {"PTS": 91.0, "REB": 27.0, "AST": 16.0, "TO": 7.0, "FG%": 0.5},
        "2": {"PTS": 79.0, "REB": 32.0, "AST": 11.0, "TO": 7.0, "FG%": 32 / 66},
    }
    league_data.upsert_matchups(
        session, league.id,
        [MatchupRecord(
            external_id="m1", period=1,
            home=MatchupSide(team_external_id="1", total_points=sum(by_period["1"].values()),
                             points_by_period=by_period["1"], category_scores=categories["1"]),
            away=MatchupSide(team_external_id="2", total_points=sum(by_period["2"].values()),
                             points_by_period=by_period["2"], category_scores=categories["2"]),
        )],
    )  # fmt: skip
    league_data.upsert_transactions(
        session, league.id,
        [TransactionRecord(
            external_id="t1", kind=TransactionKind.FREE_AGENT, status=TransactionStatus.EXECUTED,
            team_external_id="3", proposed_at=datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
            scoring_period=2,
            items=(TransactionItem(
                player=PlayerKey(source=IdSource(platform.value), external_id=pickup_id),
                action=ItemAction.ADD, to_team="3",
            ),),
        )],
    )  # fmt: skip

    def projection(
        name: str, stats: dict[Stat, float], day: date | None = None
    ) -> tuple[ProjectionRecord, UUID]:
        record = ProjectionRecord(player=key(name), per_game=stats, games=1.0, game_date=day)
        return record, ids[name]

    def per_game(pts: float, reb: float, ast: float) -> dict[Stat, float]:
        return {
            Stat.POINTS: pts, Stat.REBOUNDS: reb, Stat.ASSISTS: ast, Stat.TURNOVERS: 2.0,
            Stat.FIELD_GOALS_MADE: pts * 0.4, Stat.FIELD_GOALS_ATTEMPTED: pts * 0.85,
        }  # fmt: skip

    def load(
        scope: ProjectionScope,
        period: str | None,
        pulled_at: datetime,
        raw_key: str,
        rows: list[tuple[ProjectionRecord, UUID]],
    ) -> None:
        nba.load_projections(
            session, provider=Provider.SLEEPER, scope=scope, season=2027, period=period,
            pulled_at=pulled_at, raw_key=raw_key, rows=rows,
        )  # fmt: skip

    week = ProjectionScope.WEEK
    load(week, "1", datetime(2026, 11, 4, 10, 0, tzinfo=UTC), "before-the-games",
         [projection(n, per_game(*v), WED) for n, v in PROJECTED.items()])  # fmt: skip
    # Pulled after the game: a different projection for A that must not be used.
    load(week, "1", datetime(2026, 11, 5, 6, 0, tzinfo=UTC), "after-the-games",
         [projection("A", {Stat.POINTS: 99.0}, WED)])  # fmt: skip
    # J has no per-game row, only a season average, and was hurt in the game: ruled out after it.
    load(ProjectionScope.SEASON, None, datetime(2026, 11, 4, 9, 0, tzinfo=UTC), "season",
         [projection("J", {Stat.POINTS: 18.0, Stat.REBOUNDS: 5.0, Stat.ASSISTS: 3.0})])  # fmt: skip
    session.add(InjuryObservationRow(
        player_id=ids["J"], provider="sleeper", status="out",
        observed_at=datetime(2026, 11, 5, 3, 0, tzinfo=UTC), note="hurt in the game",
    ))  # fmt: skip
    session.flush()
    return World(league, ids, game_ids, pickup_id)


def sheet(session: Session, world: World, now: datetime = NOW) -> DailySheet:
    return load_daily(session, world.league, now=now)


def player(s: DailySheet, world: World, name: str):  # type: ignore[no-untyped-def]
    return next(p for p in s.performances if p.player_id == world.players[name])


# ------------------------------------------------------------------------- a points league


def test_a_points_leagues_night_in_its_own_scoring(session: Session) -> None:
    world = build_world(session)
    s = sheet(session, world)
    assert (s.game_day, s.for_date, s.week, s.games) == (WED, date(2026, 11, 5), 1, 1)
    assert s.value.is_points
    assert s.lineup_known

    # Every player's night, valued by this league's rules (PTS + REB + 2 * AST).
    values = {n: player(s, world, n).value for n in "ABCDFGHIJ"}
    assert values == {
        "A": 54,
        "B": 14,
        "C": 40,
        "D": 22,
        "F": 20,
        "G": 66,
        "H": 41,
        "I": 34,
        "J": 31,
    }
    sat = player(s, world, "E")
    assert (sat.played, sat.value) == (False, 0.0)
    assert [p.name for p in s.performances[:3]] == [
        "Player G",
        "Player A",
        "Player H",
    ]  # best first

    # Ownership is Wednesday's lineup: I is on no team.
    assert (player(s, world, "A").owner, player(s, world, "A").slot) == ("1", "UTIL")
    assert (player(s, world, "C").owner, player(s, world, "C").slot) == ("1", "BENCH")
    assert player(s, world, "I").owner is None

    # Expected: the projection pulled before the tip (not the later one), with his own games.
    a = player(s, world, "A")
    assert a.expected is not None
    assert a.expected.mean == pytest.approx(blended_mean(36.0, [29.0, 38.0]))
    assert a.expected.mean == pytest.approx(427 / 12)
    assert player(s, world, "D").expected.mean == pytest.approx(33.75)
    assert player(s, world, "B").expected.mean == pytest.approx(335 / 11)
    assert player(s, world, "C").expected is None  # nothing projected for him: nothing to say
    assert len(s.projection_snapshot_ids) == 2  # the early week snapshot and the season one
    # Hurt in the game and ruled out after it: before it, he was expected to play.
    j = player(s, world, "J")
    assert j.expected is not None
    assert j.expected_to_play
    assert j.expected.mean == pytest.approx(18 + 5 + 6)
    # E was projected to play (25) and didn't.
    assert sat.expected_to_play

    # What a good game is: the quartile of every game played before last night (1..40 and the
    # eleven named games on Monday and Tuesday).
    earlier = [float(i) for i in range(1, 41)] + [29, 15, 50, 30, 14, 11, 38, 45, 22]
    assert s.good_line == pytest.approx(good_game_line(earlier))

    # The matchup: lineup x box score, day by day, and ESPN's own numbers agree (4 of 4).
    m = s.matchup
    assert m is not None
    assert (m.mode, m.week, m.final) == (MatchupMode.POINTS, 1, False)
    assert (m.me.name, m.them.name) == ("My Team", "Rivals")
    assert (m.me.day_points, m.them.day_points) == (68.0, 22.0)
    assert (m.me.week_points, m.them.week_points) == (150.0, 133.0)
    assert (m.me.reported_day, m.me.reported_week) == (68.0, 150.0)
    assert (m.them.reported_day, m.them.reported_week) == (22.0, 133.0)
    assert (m.checked.matched, m.checked.total, m.checked.gaps) == (4, 4, ())

    # The pickup: H was added by team 3 on Tuesday.
    (pick,) = s.pickups
    assert (pick.performance.name, pick.team, pick.added_day, pick.kind) == (
        "Player H", "3", "2026-11-03", "free_agent",
    )  # fmt: skip


def test_the_cards_say_it_in_the_leagues_points(session: Session) -> None:
    world = build_world(session)
    cards = {c.id: c for c in recap_cards(sheet(session, world))}
    assert list(cards) == [
        "daily-matchup", "daily-my-team", "daily-standouts", "daily-pickups", "daily-surprises",
    ]  # fmt: skip

    matchup = cards["daily-matchup"]
    assert matchup.kind is CardKind.MATCHUP
    assert matchup.title == "Last night: you 68.0, Rivals 22.0"
    assert matchup.body == (
        "Last night (Wed Nov 4): you 68.0, Rivals 22.0. Week 1 so far: you 150.0, Rivals 133.0 "
        "(you lead Rivals by 17.0). Our totals match ESPN's own (4 of 4)."
    )
    assert matchup.data["checked"] == {"matched": 4, "total": 4}

    standouts = cards["daily-standouts"]
    assert standouts.kind is CardKind.RECAP
    assert standouts.title == "Last night's top game: Player G, 66.0"
    assert standouts.body == (
        "The best games of Wed Nov 4: Player G 66.0 (Third Team); Player A 54.0 (yours); "
        "Player H 41.0 (Third Team); Player C 40.0 (yours); Player I 34.0 (free agent)."
    )

    pickups = cards["daily-pickups"]
    assert pickups.kind is CardKind.LEAGUE_MOVES
    assert pickups.title == "A recent pickup paid off: Player H, 41.0"
    assert "added by Third Team on Tue Nov 3, expected 15.0" in pickups.body

    mine = cards["daily-my-team"]
    # A (54, expected 35.58) beat it, B (14, expected 30.45) missed it; C is on the bench.
    assert mine.title == "Your starters: 68.0 against 66.0 expected"
    assert "Beat the projection: Player A 54.0 (expected 35.6)." in mine.body
    assert "Missed it: Player B 14.0 (expected 30.5)." in mine.body
    assert "On your bench, with a good game: Player C 40.0." in mine.body

    surprises = cards["daily-surprises"]
    assert "Player D 22.0 against 33.8 expected (Rivals)" in surprises.body  # a worse night
    assert "Player E (Rivals)" in surprises.body  # expected to play, sat out
    assert "Player G 66.0 against 39.0 expected" in surprises.body
    assert (
        "Player I 34.0 against 15.0 expected (free agent)" in surprises.body
    )  # anyone's, owned or not
    assert "Player C" not in surprises.body  # nothing was projected for him: nothing to compare


def test_a_totals_gap_against_the_platform_is_reported_not_hidden(session: Session) -> None:
    # ESPN says the opponent scored 20.0 on Wednesday, where our box scores and its lineup say 22.0.
    world = build_world(session, reported={
        "1": {1: 44.0, 2: 38.0, 3: 68.0}, "2": {1: 44.0, 2: 67.0, 3: 20.0},
    })  # fmt: skip
    s = sheet(session, world)
    assert s.matchup is not None
    assert s.matchup.checked.total == 4
    assert s.matchup.checked.matched == 2  # their day and their week both differ
    assert s.matchup.checked.gaps == (2.0, 2.0)
    body = {c.id: c for c in recap_cards(s)}["daily-matchup"].body
    assert "Our totals differ from ESPN's own in 2 of 4" in body
    assert "you 68.0, Rivals 22.0" in body  # the number shown is still ours


def test_a_day_without_a_saved_lineup_leaves_the_totals_unknown_not_zero(session: Session) -> None:
    world = build_world(session, lineups_on=(MON, WED))  # Tuesday's lineup was never saved
    m = sheet(session, world).matchup
    assert m is not None
    assert (m.me.day_points, m.them.day_points) == (68.0, 22.0)  # Wednesday is known
    assert (m.me.week_points, m.them.week_points) == (None, None)  # the week isn't
    assert m.checked.total == 2  # the days still check
    body = {c.id: c for c in recap_cards(sheet(session, world))}["daily-matchup"].body
    assert "The week's total isn't known" in body

    world = build_world(session, lineups_on=(MON, TUE))  # last night's own lineup is missing
    m = sheet(session, world).matchup
    assert m is not None
    assert (m.me.day_points, m.me.week_points) == (None, None)
    card = {c.id: c for c in recap_cards(sheet(session, world))}["daily-matchup"]
    assert "lineups weren't saved" in card.body
    assert "ESPN's totals are not used in their place" in card.body


def test_a_night_with_no_games_has_nothing_to_recap(session: Session) -> None:
    world = build_world(session)
    s = sheet(session, world, now=datetime(2026, 11, 8, 11, 20, tzinfo=UTC))  # last night: Saturday
    assert s.games == 0
    assert s.performances == ()
    (card,) = recap_cards(s)
    assert card.id == "daily-no-games"
    assert card.title == "No NBA games on Sat Nov 7"


# ------------------------------------------------------------------------- a categories league


def test_a_categories_league_counts_each_category_for_the_week(session: Session) -> None:
    world = build_world(session, scoring=CATEGORY_RULES)
    s = sheet(session, world)
    assert not s.value.is_points
    m = s.matchup
    assert m is not None
    assert m.mode is MatchupMode.CATEGORIES
    # Both sides' starters over Monday to Wednesday (worked out in the module docstring's data):
    assert m.me.week_categories == pytest.approx(
        {"PTS": 91.0, "REB": 27.0, "AST": 16.0, "TO": 7.0, "FG%": 0.5}
    )
    assert m.them.week_categories == pytest.approx(
        {"PTS": 79.0, "REB": 32.0, "AST": 11.0, "TO": 7.0, "FG%": 32 / 66}
    )
    assert m.me.day_categories == pytest.approx(
        {"PTS": 39.0, "REB": 11.0, "AST": 9.0, "TO": 4.0, "FG%": 14 / 29}
    )  # A and B on Wednesday
    assert m.them.day_categories["PTS"] == 15.0  # D only: E sat out
    assert (m.checked.matched, m.checked.total) == (10, 10)  # ESPN's running totals agree

    card = {c.id: c for c in recap_cards(s)}["daily-matchup"]
    assert card.kind is CardKind.MATCHUP
    assert card.title == "Week 1 vs Rivals: so far 3-1-1"
    assert card.body.startswith(
        "Week 1 so far against Rivals through Wed Nov 4 (won-lost-tied: 3-1-1): "
        "you lead PTS, AST, FG%; you trail in REB; tied in TO."
    )
    assert "Our totals match ESPN's own (10 of 10)." in card.body


def test_a_categories_league_values_games_as_an_index_and_still_ranks_them(
    session: Session,
) -> None:
    world = build_world(session, scoring=CATEGORY_RULES)
    s = sheet(session, world)
    top = s.performances[0]
    assert top.name == "Player G"  # 42 points, 12 rebounds, 6 assists: the biggest night
    cards = {c.id: c for c in recap_cards(s)}
    assert cards["daily-standouts"].title.startswith("Last night's top game: Player G, +")
    assert "42 PTS" in cards["daily-standouts"].body  # the stat line stands in for points
    assert "no points" in (cards["daily-standouts"].reason or "")


def test_the_platforms_category_totals_disagreeing_is_reported(session: Session) -> None:
    world = build_world(session, scoring=CATEGORY_RULES)
    m = sheet(session, world).matchup
    assert m is not None
    assert m.checked.total == 10
    # Change ESPN's FG% for the opponent: only that category differs (a tolerance of 0.001).
    league_data.upsert_matchups(
        session, world.league.id,
        [MatchupRecord(
            external_id="m1", period=1,
            home=MatchupSide(team_external_id="1", category_scores={
                "PTS": 91.0, "REB": 27.0, "AST": 16.0, "TO": 7.0, "FG%": 0.5}),
            away=MatchupSide(team_external_id="2", category_scores={
                "PTS": 79.0, "REB": 32.0, "AST": 11.0, "TO": 7.0, "FG%": 0.52}),
        )],
    )  # fmt: skip
    m = sheet(session, world).matchup
    assert m is not None
    assert (m.checked.matched, m.checked.total) == (9, 10)


# ------------------------------------------------------------------------- a lock-in league


def test_a_lock_in_league_shows_game_scores_and_the_platforms_own_score(session: Session) -> None:
    world = build_world(session, platform=Platform.SLEEPER, lock_in=True)
    # Sleeper's own score for the week so far (it depends on locks the app can't see).
    league_data.upsert_matchups(
        session, world.league.id,
        [MatchupRecord(
            external_id="m1", period=1,
            home=MatchupSide(team_external_id="1", total_points=123.5),
            away=MatchupSide(team_external_id="2", total_points=98.0),
        )],
    )  # fmt: skip
    s = sheet(session, world)
    m = s.matchup
    assert m is not None
    assert m.mode is MatchupMode.LOCK_IN
    # Last night's games before locks: A 54 + B 14; D 22 (E sat out). No total of ours.
    assert (m.me.day_points, m.them.day_points) == (68.0, 22.0)
    assert (m.me.week_points, m.them.week_points) == (None, None)
    assert (m.me.reported_week, m.them.reported_week) == (123.5, 98.0)
    assert m.checked.total == 0  # nothing of ours to check, and nothing guessed

    card = {c.id: c for c in recap_cards(s)}["daily-matchup"]
    assert card.title == "Week 1 vs Rivals: Sleeper shows 123.5 to 98.0"
    assert "worth 68.0 in this league's scoring" in card.body
    assert "only a locked game counts" in card.body
    assert "Sleeper's own score for week 1 so far: you 123.5, Rivals 98.0." in card.body
    assert "can't see locks" in (card.reason or "")
    # The other blocks are the same as in any league.
    assert "daily-standouts" in {c.id for c in recap_cards(s)}


# ------------------------------------------------------------------------- the rest


def test_every_block_can_be_switched_off_by_name(session: Session) -> None:
    world = build_world(session)
    s = sheet(session, world)
    assert [c.id for c in recap_cards(s, only={"standouts", "matchup"})] == [
        "daily-matchup", "daily-standouts",
    ]  # fmt: skip
    assert recap_cards(s, only=set()) == []


def test_a_week_that_began_before_this_one_has_no_matchup_card(session: Session) -> None:
    # ESPN's matchup holds points from scoring day 0: it began before this calendar week.
    world = build_world(session, reported={
        "1": {0: 10.0, 1: 44.0, 2: 38.0, 3: 68.0}, "2": {0: 5.0, 1: 44.0, 2: 67.0, 3: 22.0},
    })  # fmt: skip
    s = sheet(session, world)
    assert s.matchup is None
    assert "daily-matchup" not in {c.id for c in recap_cards(s)}
    assert "daily-standouts" in {c.id for c in recap_cards(s)}  # the rest is unaffected


def test_a_pickup_before_the_window_isnt_listed(session: Session) -> None:
    world = build_world(session)
    # Added 8 days before last night: outside the 7-day window.
    league_data.upsert_transactions(
        session, world.league.id,
        [TransactionRecord(
            external_id="t1", kind=TransactionKind.FREE_AGENT, status=TransactionStatus.EXECUTED,
            team_external_id="3", proposed_at=WED_START - timedelta(days=7, hours=1),
            scoring_period=1,
            items=(TransactionItem(
                player=PlayerKey(source=IdSource.ESPN, external_id=world.pickup_id),
                action=ItemAction.ADD, to_team="3",
            ),),
        )],
    )  # fmt: skip
    assert sheet(session, world).pickups == ()


WED_START = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)  # midnight Eastern


def test_the_lineup_fetch_always_covers_last_night_even_on_the_first_day_of_a_week(
    session: Session,
) -> None:
    build_world(session)  # the schedule opens on Monday 2026-11-02, scoring period 1

    def days(today: date) -> list[str]:
        return [d.isoformat() for d, _ in _finished_days(session, 2027, today)]

    # Midweek: every day of the matchup week so far.
    assert days(date(2026, 11, 5)) == ["2026-11-02", "2026-11-03", "2026-11-04"]
    # The first day of a week: last night was the Sunday that ended the one before.
    assert days(date(2026, 11, 9)) == ["2026-11-08"]
    # The day after that: just the new week's Monday.
    assert days(date(2026, 11, 10)) == ["2026-11-09"]
    # Opening day itself has no night before it.
    assert days(date(2026, 11, 2)) == []
    # Each day comes with the ESPN scoring period it belongs to (opening night is period 1).
    assert _finished_days(session, 2027, date(2026, 11, 9)) == [(date(2026, 11, 8), 7)]
