"""The daily "what happened" cards from a hand-built sheet (no database): wording, which blocks
appear, and the limits a Card enforces. The same cards built from a stored league are in
tests/integration/test_daily_recap.py."""

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from fantasy_helper.domain.league import (
    League,
    LeagueRules,
    LineupMode,
    Platform,
    RosterSlot,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.reports import Card, CardKind
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.recap import GameValue, Performance, Pickup
from fantasy_helper.engine.reconcile import Agreement
from fantasy_helper.facts.daily import DailySheet, MatchupDay, MatchupMode, Side
from fantasy_helper.reports.recap import BLOCKS, recap_cards

POINTS = ScoringRules(scoring_type=ScoringType.POINTS, weights={Stat.POINTS: 1.0})
RULES = LeagueRules(
    team_count=2, scoring=POINTS, lineup_mode=LineupMode.DAILY,
    roster_slots=(RosterSlot.named("UTIL", 2), RosterSlot.named("BENCH", 2)),
)  # fmt: skip
ME, THEM = "1", "2"


def perf(
    name: str,
    value: float,
    *,
    owner: str | None = None,
    slot: str | None = None,
    expected: tuple[float, float] | None = None,
    played: bool = True,
) -> Performance:
    dist = None if expected is None else GameDistribution(expected[0], expected[1], 1.0)
    return Performance(
        player_id=uuid4(), name=name, nba_team="BOS", played=played, value=value,
        line={Stat.POINTS: value}, expected=dist, owner=owner, slot=slot,
    )  # fmt: skip


def side(team: str, name: str, day: float | None, week: float | None) -> Side:
    return Side(team=team, name=name, day_points=day, week_points=week)


def matchup(
    me: Side, them: Side, *, final: bool = False, checked: Agreement | None = None
) -> MatchupDay:
    return MatchupDay(MatchupMode.POINTS, 1, final, me, them, checked or Agreement(0, 0))


def sheet(
    performances: tuple[Performance, ...] = (),
    *,
    games: int = 5,
    good_line: float | None = 30.0,
    matchup: MatchupDay | None = None,
    pickups: tuple[Pickup, ...] = (),
) -> DailySheet:
    league = League(
        user_id=uuid4(), platform=Platform.ESPN, external_id="L", season=2027, name="Test League",
        my_team_external_id=ME,
    )  # fmt: skip
    return DailySheet(
        league=league, rules=RULES, settings_version=1,
        for_date=date(2026, 11, 5), game_day=date(2026, 11, 4), week=1,
        value=GameValue.for_league(POINTS, []), games=games, performances=performances,
        good_line=good_line, my_team=ME, team_names={ME: "My Team", THEM: "Rivals"},
        lineup_known=True, matchup=matchup, pickups=pickups, projection_snapshot_ids=(),
    )  # fmt: skip


def by_id(cards: list[Card]) -> dict[str, Card]:
    return {c.id: c for c in cards}


def test_a_night_with_games_but_nothing_to_say_is_a_quiet_night() -> None:
    (card,) = recap_cards(sheet(()))
    assert card.id == "daily-quiet"
    assert card.title == "A quiet night on Wed Nov 4"
    assert card.kind is CardKind.RECAP
    # Switching blocks off by name never adds the quiet card: nothing asked for is nothing shown.
    assert recap_cards(sheet(()), only={"matchup"}) == []


def test_no_games_is_one_card_whatever_else_the_sheet_holds() -> None:
    busy = sheet(
        (perf("A", 50.0),), games=0, matchup=matchup(side(ME, "x", 1, 1), side(THEM, "y", 1, 1))
    )
    (card,) = recap_cards(busy)
    assert (card.id, card.title) == ("daily-no-games", "No NBA games on Wed Nov 4")


def test_each_block_appears_only_when_it_has_something_to_say() -> None:
    only_a_matchup = sheet(
        (), matchup=matchup(side(ME, "My Team", 10.0, 10.0), side(THEM, "Rivals", 5.0, 5.0))
    )
    assert list(by_id(recap_cards(only_a_matchup))) == ["daily-matchup"]
    only_a_game = sheet((perf("A", 20.0),))
    assert list(by_id(recap_cards(only_a_game))) == ["daily-standouts"]
    # Nobody played: nothing to rank.
    assert recap_cards(sheet((perf("A", 0.0, played=False),))) != []  # the quiet card
    assert "daily-standouts" not in by_id(recap_cards(sheet((perf("A", 0.0, played=False),))))
    # A pickup without a good-game line yet (too early in the season) is never called good.
    early = sheet(
        (perf("A", 50.0),), good_line=None,
        pickups=(Pickup(perf("A", 50.0), "2", "2026-11-03", "waiver"),),
    )  # fmt: skip
    assert "daily-pickups" not in by_id(recap_cards(early))


def test_blocks_can_be_picked_by_name_and_keep_their_order() -> None:
    s = sheet(
        (perf("Mine", 40.0, owner=ME, slot="UTIL", expected=(30.0, 5.0)),),
        matchup=matchup(side(ME, "My Team", 40.0, 40.0), side(THEM, "Rivals", 10.0, 10.0)),
    )
    # 40 against 30 expected, in spreads of 5: a surprise as well as a standout.
    assert list(by_id(recap_cards(s))) == [
        "daily-matchup", "daily-my-team", "daily-standouts", "daily-surprises",
    ]  # fmt: skip
    assert list(by_id(recap_cards(s, only={"standouts", "matchup"}))) == [
        "daily-matchup", "daily-standouts",
    ]  # fmt: skip
    assert recap_cards(s, only=set()) == []
    assert set(BLOCKS) == {"matchup", "my-team", "standouts", "pickups", "surprises"}


def test_the_matchups_last_day_reads_as_a_final() -> None:
    def card(me: float, them: float) -> str:
        m = matchup(side(ME, "My Team", 5.0, me), side(THEM, "Rivals", 5.0, them), final=True)
        return by_id(recap_cards(sheet((), matchup=m)))["daily-matchup"].body

    assert "Week 1 final: you 120.0, Rivals 100.0 (you won)." in card(120.0, 100.0)
    assert "Week 1 final: you 90.0, Rivals 100.0 (you lost)." in card(90.0, 100.0)
    assert "Week 1 final: you 100.0, Rivals 100.0 (tied)." in card(100.0, 100.0)


def test_a_close_week_says_tied_not_a_lead_of_nothing() -> None:
    m = matchup(side(ME, "My Team", 5.0, 100.0), side(THEM, "Rivals", 5.0, 100.0))
    body = by_id(recap_cards(sheet((), matchup=m)))["daily-matchup"].body
    assert "(tied)" in body
    behind = matchup(side(ME, "My Team", 5.0, 90.0), side(THEM, "Rivals", 5.0, 100.0))
    assert (
        "(Rivals leads by 10.0)"
        in by_id(recap_cards(sheet((), matchup=behind)))["daily-matchup"].body
    )


def test_unknown_totals_are_said_to_be_unknown_never_shown_as_zero() -> None:
    m = matchup(side(ME, "My Team", None, None), side(THEM, "Rivals", None, None),
                checked=Agreement(0, 0))  # fmt: skip
    card = by_id(recap_cards(sheet((), matchup=m)))["daily-matchup"]
    assert "0.0" not in card.body
    assert "last night's points unknown" in card.title
    assert card.data["me"]["day_points"] is None  # type: ignore[index,call-overload]


def test_a_bench_player_with_a_good_game_is_pointed_out_and_a_poor_one_is_not() -> None:
    s = sheet((
        perf("Starter", 20.0, owner=ME, slot="UTIL", expected=(20.0, 4.0)),
        perf("Big Bench", 45.0, owner=ME, slot="BENCH"),
        perf("Small Bench", 12.0, owner=ME, slot="BENCH"),
        perf("IR Guy", 60.0, owner=ME, slot="IR"),
    ))  # fmt: skip
    body = by_id(recap_cards(s))["daily-my-team"].body
    assert "On your bench, with a good game: Big Bench 45.0, IR Guy 60.0." in body
    assert "Small Bench" not in body


def test_a_long_night_still_fits_a_cards_limits() -> None:
    owners = [None, ME, THEM, "3"]
    many = tuple(
        perf(f"Player With A Rather Long Name {i}", 50.0 + i, owner=owners[i % 4],
             slot="UTIL" if i % 4 == 1 else None, expected=(30.0, 3.0))
        for i in range(40)
    )  # fmt: skip
    pickups = tuple(Pickup(p, "3", "2026-11-03", "free_agent") for p in many[:12])
    s = replace(
        sheet(many, pickups=pickups),
        team_names={ME: "My Team", THEM: "R" * 60, "3": "Third " * 12},
    )
    for card in recap_cards(s):  # building a Card at all enforces the 120 / 1000 character limits
        assert 0 < len(card.title) <= 120
        assert 0 < len(card.body) <= 1000


@pytest.mark.parametrize("platform", [Platform.ESPN, Platform.SLEEPER])
def test_the_matchup_card_names_the_platform_its_totals_were_checked_against(
    platform: Platform,
) -> None:
    base = sheet((), matchup=matchup(
        side(ME, "My Team", 10.0, 10.0), side(THEM, "Rivals", 8.0, 8.0), checked=Agreement(3, 4)
    ))  # fmt: skip
    s = replace(base, league=base.league.model_copy(update={"platform": platform}))
    body = by_id(recap_cards(s))["daily-matchup"].body
    name = "ESPN" if platform is Platform.ESPN else "Sleeper"
    assert f"Our totals differ from {name}'s own in 1 of 4" in body
