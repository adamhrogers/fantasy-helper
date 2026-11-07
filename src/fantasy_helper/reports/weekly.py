"""The weekly overview: one ReportDocument per league, cards written by code from the FactSheet
(and, for moves and the matchup forecast, from what the registry logged)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from fantasy_helper.domain.facts import FactSheet
from fantasy_helper.domain.recommendations import CallType, Recommendation
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind

BELOW_REPLACEMENT_SHOWN = 3


def weekly_report(
    sheet: FactSheet,
    calls: Sequence[tuple[str, Recommendation]],
    *,
    user_id: UUID,
    generated_at: datetime,
    lock_in: bool = False,
) -> ReportDocument:
    by_type = {rec.call_type: rec for _, rec in calls}
    moves = [rec for _, rec in calls if rec.call_type is CallType.STREAM]
    cards = [c for c in (_matchup(sheet, by_type.get(CallType.MATCHUP_ODDS), lock_in),) if c]
    cards.append(_games(sheet, lock_in))
    cards += [_move(i, rec) for i, rec in enumerate(moves)]
    below = _below_replacement(sheet)
    if below is not None:
        cards.append(below)
    return ReportDocument(
        user_id=user_id,
        league_id=sheet.league_id,
        kind=ReportKind.WEEKLY,
        for_date=sheet.from_day,
        generated_at=generated_at,
        cards=tuple(cards),
        engine_only=True,
    )


def _span(sheet: FactSheet) -> str:
    return f"{sheet.from_day:%a %b} {sheet.from_day.day} to Sunday"


def _matchup(sheet: FactSheet, rec: Recommendation | None, lock_in: bool) -> Card | None:
    m = sheet.matchup
    if m is None:
        return None
    body = (
        f"Projected for {_span(sheet)}: you {m.my_points:.0f} (± {m.my_sd:.0f}), "
        f"{m.opponent} {m.their_points:.0f} (± {m.their_sd:.0f})."
    )
    if m.win_probability is None:
        body += " Mid-week, so the score so far isn't included: no win probability."
        title = f"Week {sheet.week} vs {m.opponent}: rest of the week"
    else:
        title = f"Week {sheet.week} vs {m.opponent}: {m.win_probability:.0%} to win"
    if lock_in:
        body += " Each starter counts the game you'd lock, playing the week out optimally."
    return Card(
        id="weekly-matchup",
        kind=CardKind.MATCHUP,
        title=title[:120],
        body=body[:1000],
        reason="A normal approximation over both teams' projected started games.",
        recommendation_id=rec.id if rec else None,
        data=m.model_dump(mode="json"),
    )


def _games(sheet: FactSheet, lock_in: bool) -> Card:
    if lock_in:
        body = (
            f"{sheet.games} games left on your active roster this week; {sheet.startable} "
            "starters have at least one game."
        )
    else:
        days = "; ".join(
            f"{d.day:%a} {d.started}/{d.slots}"
            + (f" ({d.games - d.started} on the bench)" if d.games > d.started else "")
            for d in sheet.days
        )
        body = (
            f"{sheet.startable} of your {sheet.games} games fit a starting slot. Started per day: "
            f"{days or 'no games left'}."
        )
    return Card(
        id="weekly-games",
        kind=CardKind.RECAP,
        title=f"Week {sheet.week}: {sheet.startable} startable games",
        body=body[:1000],
        data={"games": sheet.games, "startable": sheet.startable},
    )


def _move(index: int, rec: Recommendation) -> Card:
    d = rec.detail
    return Card(
        id=f"weekly-move-{index}",
        kind=CardKind.MOVE,
        title=f"Add {d['add_name']}, drop {d['drop_name']}"[:120],
        body=(
            f"+{float(d['gain']):.1f} expected points this week: {d['games_added']} more games in "  # type: ignore[arg-type]
            "a starting slot."
        ),
        reason=f"Comes out ahead {rec.confidence:.0%} of the time, judging by the games' spread.",
        recommendation_id=rec.id,
        data=dict(d),
    )


def _below_replacement(sheet: FactSheet) -> Card | None:
    if not sheet.points_league:
        return None
    below = sorted(
        (p for p in sheet.my_players
         if p.slot != "IR" and p.value_over_replacement is not None
         and p.value_over_replacement < 0),
        key=lambda p: p.value_over_replacement or 0.0,
    )[:BELOW_REPLACEMENT_SHOWN]  # fmt: skip
    if not below:
        return None
    return Card(
        id="weekly-below-replacement",
        kind=CardKind.RECAP,
        title="Below replacement on the season",
        body=(
            "Each scores less a game than the best free agent who could fill his slot: "
            + ", ".join(f"{p.name} ({p.value_over_replacement:+.1f})" for p in below)
            + "."
        )[:1000],
    )
