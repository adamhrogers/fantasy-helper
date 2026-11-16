"""The daily report, engine-only until M4: one ReportDocument per league, cards written by code.

For a lock-in league: one lock-or-pass card per open decision, read from the registry entry so
the card says exactly what was logged, plus a recap. The app can't see locks yet (Sleeper's
numbers don't show them), so the recap says to skip anyone already locked. Every league, lock-in
or not, also gets the "what happened" cards (`reports.recap`), after any decisions.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.recommendations import Action, Recommendation
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind
from fantasy_helper.facts.lock_in import LockInSheet, StarterWeek


def lock_in_cards(
    sheet: LockInSheet, calls: Sequence[tuple[StarterWeek, Recommendation]]
) -> list[Card]:
    """One lock-or-pass card per call, then the recap; a card saying so if there is nothing."""
    cards = [_lock_card(starter, rec) for starter, rec in calls]
    decided = {starter.player_id for starter, _ in calls}
    recap = _recap(sheet, decided)
    if recap is not None:
        cards.append(recap)
    if not cards:
        cards.append(
            Card(
                id="lock-in-none",
                kind=CardKind.RECAP,
                title="No lock decisions today",
                body="None of your starters has an unlocked game with games still to come.",
            )
        )
    return cards


def lock_in_unavailable() -> Card:
    """Shown instead of the lock cards when they couldn't be built, so a missing card is never
    read as "nothing to decide"."""
    return Card(
        id="lock-in-unavailable",
        kind=CardKind.RECAP,
        title="Lock-or-pass calls weren't built today",
        body=(
            "Something went wrong building today's lock-or-pass calls, so none are shown. "
            "Check your starters yourself before their next game tips off."
        ),
    )


def daily_report(
    *,
    league_id: UUID,
    user_id: UUID,
    for_date: date,
    generated_at: datetime,
    cards: Sequence[Card],
) -> ReportDocument:
    """One league's daily report: its cards, in order (decisions first, then what happened)."""
    return ReportDocument(
        user_id=user_id,
        league_id=league_id,
        kind=ReportKind.DAILY,
        for_date=for_date,
        generated_at=generated_at,
        cards=tuple(cards),
        engine_only=True,
    )


def lock_in_report(
    sheet: LockInSheet,
    calls: Sequence[tuple[StarterWeek, Recommendation]],
    *,
    user_id: UUID,
    generated_at: datetime,
) -> ReportDocument:
    return daily_report(
        league_id=sheet.league.id,
        user_id=user_id,
        for_date=sheet.for_date,
        generated_at=generated_at,
        cards=lock_in_cards(sheet, calls),
    )


def _tip(at: str | datetime) -> str:
    moment = datetime.fromisoformat(at) if isinstance(at, str) else at
    local = moment.astimezone(NBA_TIMEZONE)
    return f"{local:%a} {local.strftime('%I:%M %p').lstrip('0')} ET"


def _lock_card(starter: StarterWeek, rec: Recommendation) -> Card:
    d: dict[str, Any] = dict(rec.detail)
    action = rec.claim.moves[0].action
    score, wait, margin = float(d["score"]), float(d["value_of_waiting"]), float(d["margin"])
    upcoming = d.get("upcoming") or []
    games = ", ".join(
        f"{_tip(g['tip_utc']).split()[0]} ~{float(g['mean']) * float(g['play_probability']):.1f}"
        for g in upcoming
    )
    title = (
        f"Lock {starter.name}: {score:.1f}"
        if action is Action.LOCK
        else f"Pass on {starter.name}'s {score:.1f}"
    )
    body = (
        f"{starter.slot} · His last game ({_day(d['latest_game_date'])}): {score:.1f} points. "
        f"Waiting is worth {wait:.1f} with {len(upcoming)} game"
        f"{'s' if len(upcoming) != 1 else ''} left ({games}). "
        f"Decide before {_tip(rec.first_tip_utc)}."
    )
    notes = []
    if d.get("near_tie"):
        notes.append("Close call: within 2 points either way.")
    edge = abs(margin)
    reason = (
        f"{'Locking' if action is Action.LOCK else 'Waiting'} is ahead by {edge:.1f} points; "
        f"this call comes out ahead in {rec.confidence:.0%} of simulated weeks."
    )
    return Card(
        id=f"lock-{starter.player_id}",
        kind=CardKind.LOCK_OR_PASS,
        title=title[:120],
        body=(body + (" " + " ".join(notes) if notes else ""))[:1000],
        reason=reason,
        recommendation_id=rec.id,
        data={k: v for k, v in d.items() if k != "prior_cv"},
    )


def _day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a")


LOCKS_NOTE = "The app can't see your locks yet: skip any player you've already locked."


def _recap(sheet: LockInSheet, decided: set[UUID]) -> Card | None:
    quiet = [s for s in sheet.starters if s.player_id not in decided]
    if not quiet and not decided:
        return None
    lines = [LOCKS_NOTE] if decided else []
    if quiet:
        lines.append(
            "Nothing to decide (no game yet this week, sat out, next game already started, or "
            "no games left): " + ", ".join(s.name for s in quiet) + "."
        )
    return Card(
        id="lock-in-recap",
        kind=CardKind.RECAP,
        title=f"Week {sheet.week}: {len(decided)} to decide",
        body=" ".join(lines)[:1000],
    )
