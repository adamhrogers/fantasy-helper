"""The weekly overview's cards, from a hand-built FactSheet (no database)."""

from datetime import UTC, date, datetime
from uuid import uuid4

from fantasy_helper.domain.facts import (
    FactAdds,
    FactDay,
    FactMatchup,
    FactPlayer,
    FactSheet,
    SheetKind,
)
from fantasy_helper.domain.recommendations import CallType
from fantasy_helper.domain.reports import CardKind
from fantasy_helper.engine.distributions import blended_mean
from fantasy_helper.reports.weekly import weekly_report
from tests.unit.test_domain import make_recommendation

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def sheet(from_day: date, win: float | None) -> FactSheet:
    return FactSheet(
        league_id=uuid4(), kind=SheetKind.WEEKLY, week=3, from_day=from_day, as_of=NOW,
        settings_version=1, points_league=True, games=30, startable=26,
        days=(FactDay(day=date(2026, 11, 2), games=9, started=8, slots=10),),
        matchup=FactMatchup(opponent="Rival", my_points=1050, my_sd=80, their_points=980,
                            their_sd=75, win_probability=win),
        my_players=(
            FactPlayer(player_id=uuid4(), name="Bench Guy", slot="BENCH", games=3, started=1,
                       per_game=12.0, value_over_replacement=-4.5),
            FactPlayer(player_id=uuid4(), name="Hurt Star", slot="IR", games=0, started=0,
                       per_game=0.0, value_over_replacement=-30.0),
        ),
    )  # fmt: skip


def test_a_whole_week_overview() -> None:
    report = weekly_report(sheet(date(2026, 11, 2), 0.74), [], user_id=uuid4(), generated_at=NOW)
    matchup, games, below = report.cards
    assert matchup.kind is CardKind.MATCHUP
    assert matchup.title == "Week 3 vs Rival: 74% to win"
    assert games.body == "26 of your 30 games fit a starting slot."  # no add limit: nothing said
    assert games.items == ("Mon: 8 of 10 slots, 1 on the bench",)
    assert below.items == ("Bench Guy: -4.5 a game",)  # IR players aren't judged
    assert matchup.body == "Projected for Mon Nov 2 to Sunday."
    assert matchup.items == ("Projected: you 1050 (± 80), Rival 980 (± 75)",)


def test_mid_week_with_points_banked() -> None:
    s = sheet(date(2026, 11, 5), 0.21)
    m = s.matchup
    assert m is not None
    so_far = m.model_copy(update={"my_banked": 512.0, "their_banked": 560.0})
    banked = s.model_copy(update={"matchup": so_far})
    card = weekly_report(banked, [], user_id=uuid4(), generated_at=NOW).cards[0]
    assert card.title == "Week 3 vs Rival: 21% to win"
    assert card.body == "Thu Nov 5 to Sunday still to play."
    assert card.items == (
        "So far: you 512, Rival 560",
        "Projected final: you 1050 (± 80), Rival 980 (± 75)",
    )


def test_mid_week_says_so_instead_of_guessing() -> None:
    report = weekly_report(sheet(date(2026, 11, 5), None), [], user_id=uuid4(), generated_at=NOW)
    assert report.cards[0].title == "Week 3 vs Rival: rest of the week"
    assert "no win probability" in report.cards[0].body


def test_projections_blend_with_what_he_actually_does() -> None:
    assert blended_mean(28.4, []) == 28.4  # no games yet: the projection alone
    eight_games = [10.875] * 8
    assert round(blended_mean(28.4, eight_games), 1) == 20.6  # (10 * 28.4 + 87) / 18


def test_the_adds_left_this_week_are_shown_where_the_league_limits_them() -> None:
    limited = sheet(date(2026, 11, 2), 0.74).model_copy(
        update={"adds": FactAdds(allowed=4, left=3)}
    )
    games = weekly_report(limited, [], user_id=uuid4(), generated_at=NOW).cards[1]
    assert games.id == "weekly-games"
    assert games.body == "26 of your 30 games fit a starting slot. 3 of 4 adds left this week."
    assert games.data["adds"] == {"allowed": 4, "left": 3}
    spent = limited.model_copy(update={"adds": FactAdds(allowed=1, left=0)})
    games = weekly_report(spent, [], user_id=uuid4(), generated_at=NOW).cards[1]
    assert games.body.endswith(" 0 of 1 add left this week.")
    # A league with no limit says nothing about adds.
    unlimited = weekly_report(sheet(date(2026, 11, 2), 0.74), [], user_id=uuid4(), generated_at=NOW)
    assert "adds" not in unlimited.cards[1].body
    assert "adds" not in unlimited.cards[1].data


def test_each_move_is_its_own_card_with_its_numbers_as_bullets() -> None:
    s = sheet(date(2026, 11, 2), 0.74)
    rec = make_recommendation(
        league_id=s.league_id, call_type=CallType.STREAM, confidence=0.71,
        detail={"add_name": "New Guy", "drop_name": "Old Guy", "gain": 12.34, "games_added": 1},
    )  # fmt: skip
    move = weekly_report(s, [("move", rec)], user_id=uuid4(), generated_at=NOW).cards[2]
    assert move.kind is CardKind.MOVE
    assert move.title == "Add New Guy, drop Old Guy"
    assert move.items == ("+12.3 expected points this week", "1 more game in a starting slot")
    assert move.recommendation_id == rec.id  # still one logged call per card
