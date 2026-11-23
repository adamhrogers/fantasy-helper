"""Who the weekly plan counted on each day, and who it left out and why (no database)."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from fantasy_helper.domain.facts import (
    FactDay,
    FactLeftOut,
    FactNoGames,
    FactSheet,
    LeftOut,
    SheetKind,
)
from fantasy_helper.domain.league import RosterSlot
from fantasy_helper.domain.player import Position
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.schedule import PlayerWeek, project_week
from fantasy_helper.facts.weekly import WeekPlayer
from fantasy_helper.pipeline.weekly import MAX_LEFT_OUT_PER_DAY, _days
from fantasy_helper.reports.weekly import weekly_report

MON, TUE = date(2026, 11, 2), date(2026, 11, 3)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SLOTS = (RosterSlot.named("UTIL", 2), RosterSlot.named("BENCH", 2), RosterSlot.named("IR", 1))
PLAYING = GameDistribution(30.0, 9.0)
NOT_PLAYING = GameDistribution(0.0, 0.0, play_probability=0.0)


def player(
    name: str,
    *games: tuple[date, GameDistribution],
    slot: str = "UTIL",
    mean: float = 30.0,
    no_projection: tuple[date, ...] = (),
    team_games: int | None = None,
) -> WeekPlayer:
    pid: UUID = uuid4()
    pos = (Position.PG,)
    return WeekPlayer(
        pid, name, "BOS", pos, slot,
        PlayerWeek(pid, pos, tuple((d, GameDistribution(mean, 9.0, g.play_probability))
                                   for d, g in games)),
        no_projection_days=no_projection, team_games_this_week=team_games,
    )  # fmt: skip


def counted(players: list[WeekPlayer]) -> tuple[FactDay, ...]:
    projection = project_week([p.week for p in players], SLOTS)
    return _days(2, players, projection)


def test_a_player_who_is_out_doesnt_fill_a_slot_or_count_as_a_game() -> None:
    hurt = player("Hurt", (MON, NOT_PLAYING), slot="UTIL")
    fit = player("Fit", (MON, PLAYING))
    projection = project_week([hurt.week, fit.week], SLOTS)
    assert projection.games == 1  # his game isn't one he's expected to play
    assert projection.startable == 1
    assert projection.started_games(hurt.player_id) == 0


def test_the_day_says_who_was_out() -> None:
    hurt = player("Hurt", (MON, NOT_PLAYING))
    fit = player("Fit", (MON, PLAYING), (TUE, PLAYING))
    monday, tuesday = counted([hurt, fit])
    assert (monday.games, monday.started, monday.slots) == (1, 1, 2)
    assert monday.left_out == (FactLeftOut(name="Hurt", reason=LeftOut.OUT, in_lineup=True),)
    assert (tuesday.games, tuesday.started) == (1, 1)
    assert tuesday.left_out == ()


def test_a_player_with_no_projection_is_named_not_silently_dropped() -> None:
    unknown = player("Unknown", no_projection=(MON,), team_games=3)
    fit = player("Fit", (MON, PLAYING))
    (monday,) = counted([unknown, fit])
    assert (monday.games, monday.started) == (1, 1)
    assert monday.left_out == (
        FactLeftOut(name="Unknown", reason=LeftOut.NO_PROJECTION, in_lineup=True),
    )


def test_a_starter_squeezed_out_is_named_but_the_bench_is_not() -> None:
    a = player("A", (MON, PLAYING), mean=40.0)
    b = player("B", (MON, PLAYING), mean=35.0)
    starter = player("Starter", (MON, PLAYING), mean=10.0, slot="UTIL")
    bench = player("Bench", (MON, PLAYING), mean=5.0, slot="BENCH")
    (monday,) = counted([a, b, starter, bench])
    assert (monday.games, monday.started) == (4, 2)
    # a player in a starting slot who didn't make the lineup is a surprise; the bench is not
    assert monday.left_out == (
        FactLeftOut(name="Starter", reason=LeftOut.OUTRANKED, in_lineup=True),
    )


def test_the_bench_isnt_noted_when_it_simply_stays_on_the_bench() -> None:
    a = player("A", (MON, PLAYING))
    bench = player("Bench", (MON, PLAYING), slot="BENCH")
    (monday,) = counted([a, bench])
    assert monday.left_out == ()  # two slots, one starter: the bench player starts, nothing to say


def test_lineup_players_come_first_and_the_list_is_capped() -> None:
    many = [player(f"Out {i:02d}", (MON, NOT_PLAYING), slot="BENCH") for i in range(12)]
    starter = player("Starter out", (MON, NOT_PLAYING), slot="UTIL")
    (monday,) = counted([*many, starter])
    assert len(monday.left_out) == MAX_LEFT_OUT_PER_DAY
    assert monday.left_out[0].name == "Starter out"
    assert monday.left_out[0].in_lineup is True
    assert (monday.games, monday.started) == (0, 0)


def test_notes_can_be_switched_off() -> None:
    hurt = player("Hurt", (MON, NOT_PLAYING))
    projection = project_week([hurt.week], SLOTS)
    (monday,) = _days(2, [hurt], projection, notes=False)
    assert monday.left_out == ()


def sheet(days: tuple[FactDay, ...], without_games: tuple[FactNoGames, ...] = ()) -> FactSheet:
    return FactSheet(
        league_id=uuid4(), kind=SheetKind.WEEKLY, week=3, from_day=MON, as_of=NOW,
        settings_version=1, points_league=True, games=9, startable=8, days=days,
        without_games=without_games, my_players=(),
    )  # fmt: skip


def card_ids(s: FactSheet) -> list[str]:
    return [c.id for c in weekly_report(s, [], user_id=uuid4(), generated_at=NOW).cards]


def test_the_report_says_who_the_plan_left_out_and_why() -> None:
    s = sheet(
        (
            FactDay(
                day=MON,
                games=9,
                started=9,
                slots=10,
                left_out=(
                    FactLeftOut(name="Hurt", reason=LeftOut.OUT, in_lineup=True),
                    FactLeftOut(name="Bench", reason=LeftOut.OUTRANKED, in_lineup=False),
                ),
            ),
            FactDay(
                day=TUE,
                games=9,
                started=9,
                slots=10,
                left_out=(FactLeftOut(name="Hurt", reason=LeftOut.OUT, in_lineup=True),),
            ),
        ),
        (FactNoGames(name="Lost", nba_team="XYZ"), FactNoGames(name="Teamless")),
    )
    card = next(
        c for c in weekly_report(s, [], user_id=uuid4(), generated_at=NOW).cards
        if c.id == "weekly-lineup"
    )  # fmt: skip
    assert card.body == "Games the plan doesn't count, and why."
    assert card.items == (  # one line per player, your lineup first
        "Hurt: in your lineup, not expected to play (Mon, Tue)",
        "Bench: better players fill the slots (Mon)",
        "Lost: no games in the schedule this week (XYZ)",
        "Teamless: no games in the schedule this week (no team on file)",
    )


def test_the_card_only_appears_when_there_is_something_to_say() -> None:
    quiet = sheet((FactDay(day=MON, games=9, started=9, slots=10),))
    assert "weekly-lineup" not in card_ids(quiet)
    no_games = sheet((), (FactNoGames(name="Lost", nba_team="XYZ"),))
    assert "weekly-lineup" in card_ids(no_games)
