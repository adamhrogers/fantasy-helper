"""The weekly overview: one ReportDocument per league, cards written by code from the FactSheet
(and, for moves and the matchup forecast, from what the registry logged)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from fantasy_helper.domain.facts import FactSheet, LeftOut
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
    lineup = _lineup(sheet)
    if lineup is not None:
        cards.append(lineup)
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
    if m.my_banked is not None and m.their_banked is not None:
        body = (
            f"So far this week: you {m.my_banked:.0f}, {m.opponent} {m.their_banked:.0f}. "
            f"Projected final, with {_span(sheet)} still to play: you {m.my_points:.0f} "
            f"(± {m.my_sd:.0f}), {m.opponent} {m.their_points:.0f} (± {m.their_sd:.0f})."
        )
    else:
        body = (
            f"Projected for {_span(sheet)}: you {m.my_points:.0f} (± {m.my_sd:.0f}), "
            f"{m.opponent} {m.their_points:.0f} (± {m.their_sd:.0f})."
        )
    if m.win_probability is None:
        body += " The score so far isn't known here (locks aren't visible), so no win probability."
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


_WHY = {
    LeftOut.OUT: "not expected to play",
    LeftOut.NO_PROJECTION: "no projection for the game",
    LeftOut.OUTRANKED: "better players fill the slots",
}


def _lineup(sheet: FactSheet) -> Card | None:
    """Who had a game but wasn't counted, and why, so a count that looks wrong can be checked
    against the league's own lineup. Only shown when there's something to say."""
    left: dict[tuple[str, LeftOut, bool], list[str]] = {}
    for d in sheet.days:
        for e in d.left_out:
            left.setdefault((e.name, e.reason, e.in_lineup), []).append(f"{d.day:%a}")
    parts = []
    for (name, reason, in_lineup), days in sorted(
        left.items(), key=lambda kv: (not kv[0][2], kv[0])
    ):
        where = "in your lineup, " if in_lineup and reason is not LeftOut.OUTRANKED else ""
        parts.append(f"{name} ({where}{_WHY[reason]}) on {', '.join(days)}")
    body = ""
    if parts:
        body += "Games the plan doesn't count: " + "; ".join(parts) + "."
    if sheet.without_games:
        who = ", ".join(
            f"{g.name} ({g.nba_team or 'no team on file'})" for g in sheet.without_games
        )
        body += f" No games found in the schedule this week for: {who}."
    if not body:
        return None
    return Card(
        id="weekly-lineup",
        kind=CardKind.RECAP,
        title="Who the plan counted, and who it left out",
        body=body.strip()[:1000],
        data={
            "days": [d.model_dump(mode="json") for d in sheet.days if d.left_out],
            "without_games": [g.model_dump(mode="json") for g in sheet.without_games],
        },
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
