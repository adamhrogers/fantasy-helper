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
from fantasy_helper.domain.reports import MAX_ITEM_LENGTH, MAX_ITEMS, Card, CardKind
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import GameDistribution
from fantasy_helper.engine.recap import GameValue, Performance, Pickup
from fantasy_helper.engine.reconcile import Agreement
from fantasy_helper.facts.daily import (
    DailySheet,
    MatchupDay,
    MatchupMode,
    Side,
    TotalsDifference,
)
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
    me: Side,
    them: Side,
    *,
    final: bool = False,
    checked: Agreement | None = None,
    differences: tuple[TotalsDifference, ...] = (),
) -> MatchupDay:
    return MatchupDay(
        MatchupMode.POINTS, 1, final, me, them, checked or Agreement(0, 0), differences
    )


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
    assert set(BLOCKS) == {"matchup", "mismatch", "my-team", "standouts", "pickups", "surprises"}


def test_the_matchups_last_day_reads_as_a_final() -> None:
    def card(me: float, them: float) -> str:
        m = matchup(side(ME, "My Team", 5.0, me), side(THEM, "Rivals", 5.0, them), final=True)
        return by_id(recap_cards(sheet((), matchup=m)))["daily-matchup"].items[1]

    assert card(120.0, 100.0) == "Week 1 final: you 120.0, Rivals 100.0 (you won)"
    assert card(90.0, 100.0) == "Week 1 final: you 90.0, Rivals 100.0 (you lost)"
    assert card(100.0, 100.0) == "Week 1 final: you 100.0, Rivals 100.0 (tied)"


def test_a_close_week_says_tied_not_a_lead_of_nothing() -> None:
    m = matchup(side(ME, "My Team", 5.0, 100.0), side(THEM, "Rivals", 5.0, 100.0))
    week = by_id(recap_cards(sheet((), matchup=m)))["daily-matchup"].items[1]
    assert week.endswith("(tied)")
    behind = matchup(side(ME, "My Team", 5.0, 90.0), side(THEM, "Rivals", 5.0, 100.0))
    week = by_id(recap_cards(sheet((), matchup=behind)))["daily-matchup"].items[1]
    assert week.endswith("(Rivals leads by 10.0)")


def test_unknown_totals_are_said_to_be_unknown_never_shown_as_zero() -> None:
    m = matchup(side(ME, "My Team", None, None), side(THEM, "Rivals", None, None),
                checked=Agreement(0, 0))  # fmt: skip
    card = by_id(recap_cards(sheet((), matchup=m)))["daily-matchup"]
    assert "0.0" not in card.body
    assert card.items == ()
    assert "last night's points unknown" in card.title
    assert card.data["me"]["day_points"] is None  # type: ignore[index,call-overload]


def test_a_bench_player_with_a_good_game_is_pointed_out_and_a_poor_one_is_not() -> None:
    s = sheet((
        perf("Starter", 20.0, owner=ME, slot="UTIL", expected=(20.0, 4.0)),
        perf("Big Bench", 45.0, owner=ME, slot="BENCH"),
        perf("Small Bench", 12.0, owner=ME, slot="BENCH"),
        perf("IR Guy", 60.0, owner=ME, slot="IR"),
    ))  # fmt: skip
    items = by_id(recap_cards(s))["daily-my-team"].items
    assert "Bench: Big Bench 45.0, a good game" in items
    assert "Bench: IR Guy 60.0, a good game" in items
    assert not any("Small Bench" in item for item in items)


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
    for card in recap_cards(s):  # building a Card at all enforces its limits
        assert 0 < len(card.title) <= 120
        assert len(card.body) <= 1000
        assert card.body or card.items  # never an empty card
        assert len(card.items) <= MAX_ITEMS
        assert all(0 < len(item) <= MAX_ITEM_LENGTH for item in card.items)


def test_totals_that_agree_with_the_platform_are_never_mentioned() -> None:
    # Adam's rule (2026-11-23): agreement is the system working, so nothing says it.
    agreed = sheet((), matchup=matchup(
        side(ME, "My Team", 10.0, 10.0), side(THEM, "Rivals", 8.0, 8.0), checked=Agreement(4, 4)
    ))  # fmt: skip
    cards = by_id(recap_cards(agreed))
    assert list(cards) == ["daily-matchup"]
    card = cards["daily-matchup"]
    shown = " ".join([card.title, card.body, *card.items, card.reason or ""]).lower()
    for word in ("match", "agree", "check", "espn"):
        assert word not in shown.replace("matchup", "")


@pytest.mark.parametrize("platform", [Platform.ESPN, Platform.SLEEPER])
def test_totals_that_disagree_get_their_own_card_with_both_numbers(platform: Platform) -> None:
    base = sheet((), matchup=matchup(
        side(ME, "My Team", 10.0, 10.0), side(THEM, "Rivals", 8.0, 8.0), checked=Agreement(2, 4),
        differences=(
            TotalsDifference(side="them", what="day", ours=8.0, theirs=6.5),
            TotalsDifference(side="them", what="week", ours=8.0, theirs=6.5),
        ),
    ))  # fmt: skip
    s = replace(base, league=base.league.model_copy(update={"platform": platform}))
    cards = by_id(recap_cards(s))
    assert list(cards) == ["daily-matchup", "daily-mismatch"]  # right after the matchup
    assert not any("differ" in item for item in cards["daily-matchup"].items)
    name = "ESPN" if platform is Platform.ESPN else "Sleeper"
    card = cards["daily-mismatch"]
    assert card.kind is CardKind.MATCHUP
    assert card.title == f"Our numbers differ from {name}'s for last night and this week"
    assert card.body.startswith(f"2 of the 4 totals we compare with {name}'s own don't agree.")
    assert "lineup or box score is probably off" in card.body
    assert card.items == (
        f"Last night (Rivals): ours 8.0, {name} 6.5",
        f"Week 1 so far (Rivals): ours 8.0, {name} 6.5",
    )
    assert card.data["differences"] == [
        {"side": "them", "what": "day", "ours": 8.0, "theirs": 6.5},
        {"side": "them", "what": "week", "ours": 8.0, "theirs": 6.5},
    ]
    # Only last night's total off: the title says so.
    assert s.matchup is not None
    night = replace(s, matchup=replace(s.matchup, differences=s.matchup.differences[:1]))
    assert by_id(recap_cards(night))["daily-mismatch"].title.endswith("for last night")


def test_a_quiet_night_on_his_team_says_none_was_far_off() -> None:
    # A points league whose starters all played close to what was expected: no bullets, so the
    # card says so instead of only counting them.
    s = sheet((
        perf("Steady", 31.0, owner=ME, slot="UTIL", expected=(30.0, 5.0)),
        perf("Also Steady", 22.0, owner=ME, slot="UTIL", expected=(20.0, 5.0)),
    ))  # fmt: skip
    card = by_id(recap_cards(s))["daily-my-team"]
    assert card.title == "Your starters: 53.0 against 50.0 expected"
    assert card.items == ()
    assert card.body == "2 of your starters played, and none was far off what was expected."


def test_with_no_lineup_stored_no_owner_is_named_and_the_card_says_why() -> None:
    s = replace(
        sheet((
            perf("Mine", 60.0, owner=ME, slot="UTIL", expected=(30.0, 5.0)),
            perf("Theirs", 50.0, owner=THEM, slot="UTIL", expected=(30.0, 5.0)),
            perf("Nobodys", 45.0),
        )),
        lineup_known=False,
    )  # fmt: skip
    cards = by_id(recap_cards(s))
    standouts = cards["daily-standouts"]
    assert standouts.items == ("Mine 60.0", "Theirs 50.0", "Nobodys 45.0")
    assert "weren't saved" in (standouts.reason or "")
    assert "weren't saved" in (cards["daily-surprises"].reason or "")
    surprises = " ".join(cards["daily-surprises"].items)
    assert "free agent" not in surprises
    assert "Rivals" not in surprises
    owners = [e["owner"] for e in cards["daily-standouts"].data["standouts"]]  # type: ignore[union-attr,index,call-overload]
    assert owners == [None, None, None]


def test_with_a_lineup_stored_owners_are_named_as_before() -> None:
    s = sheet((perf("Mine", 60.0, owner=ME, slot="UTIL"), perf("Nobodys", 45.0)))
    card = by_id(recap_cards(s))["daily-standouts"]
    assert card.body == "The best games of Wed Nov 4."
    assert card.items == ("Mine 60.0 (yours)", "Nobodys 45.0 (free agent)")


def lock_in(
    me: float | None, them: float | None, reported: tuple[float, float] | None
) -> MatchupDay:
    mine = reported[0] if reported else None
    theirs = reported[1] if reported else None
    return MatchupDay(
        MatchupMode.LOCK_IN, 1, False,
        Side(team=ME, name="My Team", day_points=me, reported_week=mine),
        Side(team=THEM, name="Rivals", day_points=them, reported_week=theirs),
        Agreement(0, 0),
    )  # fmt: skip


def test_a_lock_in_figure_is_labelled_as_game_scores_not_a_total() -> None:
    card = by_id(recap_cards(sheet((), matchup=lock_in(68.0, 22.0, (123.5, 98.0)))))[
        "daily-matchup"
    ]
    assert "Last night's games, before locks: you 68.0, Rivals 22.0" in card.items
    assert "game scores before locks, not your score" in card.body
    assert "not a comparable total" in (card.reason or "")
    assert card.data["day_points_are"] == "game scores before locks, not a total"
    assert card.title == "Week 1 vs Rivals: ESPN shows 123.5 to 98.0"


def test_a_lock_in_card_with_unknown_game_scores_says_so_and_keeps_the_platforms_score() -> None:
    card = by_id(recap_cards(sheet((), matchup=lock_in(None, 22.0, (123.5, 98.0)))))[
        "daily-matchup"
    ]
    assert "lineups weren't saved" in card.body
    assert card.items == ("ESPN's own score for week 1 so far: you 123.5, Rivals 98.0",)
    plain = by_id(recap_cards(sheet((), matchup=lock_in(None, None, None))))["daily-matchup"]
    assert plain.title == "Week 1 vs Rivals: last night's games"
