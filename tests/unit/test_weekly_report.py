"""The weekly overview's cards, from a hand-built FactSheet (no database)."""

from datetime import UTC, date, datetime
from uuid import uuid4

from fantasy_helper.domain.facts import FactDay, FactMatchup, FactPlayer, FactSheet, SheetKind
from fantasy_helper.domain.reports import CardKind
from fantasy_helper.engine.distributions import blended_mean
from fantasy_helper.reports.weekly import weekly_report

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
    assert "Mon 8/10 (1 on the bench)" in games.body
    assert "Bench Guy (-4.5)" in below.body
    assert "Hurt Star" not in below.body  # IR players aren't judged


def test_mid_week_with_points_banked() -> None:
    s = sheet(date(2026, 11, 5), 0.21)
    m = s.matchup
    assert m is not None
    so_far = m.model_copy(update={"my_banked": 512.0, "their_banked": 560.0})
    banked = s.model_copy(update={"matchup": so_far})
    card = weekly_report(banked, [], user_id=uuid4(), generated_at=NOW).cards[0]
    assert card.title == "Week 3 vs Rival: 21% to win"
    assert card.body.startswith("So far this week: you 512, Rival 560.")
    assert "with Thu Nov 5 to Sunday still to play" in card.body


def test_mid_week_says_so_instead_of_guessing() -> None:
    report = weekly_report(sheet(date(2026, 11, 5), None), [], user_id=uuid4(), generated_at=NOW)
    assert report.cards[0].title == "Week 3 vs Rival: rest of the week"
    assert "no win probability" in report.cards[0].body


def test_projections_blend_with_what_he_actually_does() -> None:
    assert blended_mean(28.4, []) == 28.4  # no games yet: the projection alone
    eight_games = [10.875] * 8
    assert round(blended_mean(28.4, eight_games), 1) == 20.6  # (10 * 28.4 + 87) / 18
