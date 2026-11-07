"""Lock-or-pass calls: turn the facts and the engine's decision into a registry entry.

Orchestration, so it lives in the pipeline: it reads facts and engine output and hands a plain
Recommendation to the registry (registry.calls.log_call), which logs it before tip-off.

One call per player per lockable game (`dedupe_key`), so rerunning the morning never logs a
second, different call: the registry keeps the first (ADR 0005).
"""

from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.recommendations import (
    Action,
    CallInputs,
    CallType,
    Claim,
    Move,
    OutcomeWindow,
    Producer,
    Recommendation,
)
from fantasy_helper.engine.lock_in import LockDecision
from fantasy_helper.facts.lock_in import GameScore, LockInSheet, StarterWeek

ENGINE_PRODUCER = Producer(agent_setup="engine", models=(), prompt_version="engine-v1")


class NotLockableError(ValueError):
    """A call needs a game to lock and a game still to come."""


def _latest(starter: StarterWeek) -> GameScore:
    if starter.latest is None:
        raise NotLockableError(f"{starter.name} has no game this week to lock")
    return starter.latest


def dedupe_key(starter: StarterWeek) -> str:
    return f"lock_or_pass:{starter.player_id}:{_latest(starter).game_id}"


def lock_or_pass_call(
    sheet: LockInSheet,
    starter: StarterWeek,
    decision: LockDecision,
    *,
    user_id: UUID,
    now: datetime,
) -> Recommendation:
    """The call for one starter. Its deadline (`first_tip_utc`) is his next game's tip-off."""
    _latest(starter)
    if not starter.upcoming:
        raise NotLockableError(f"{starter.name} has no games left this week")
    next_tip = starter.upcoming[0].tip_utc
    # The outcome is known once the week's last game is over: grade it the next morning.
    week_over = datetime.combine(sheet.week_end + timedelta(days=1), time(8), NBA_TIMEZONE)
    return Recommendation(
        created_at=now,
        league_id=sheet.league.id,
        user_id=user_id,
        platform=sheet.league.platform,
        format=sheet.rules.format,
        call_type=CallType.LOCK_OR_PASS,
        claim=_claim(starter, decision.action, decision, next_tip),
        expected_value=decision.expected_value,
        confidence=decision.confidence,
        baseline_call=_claim(starter, decision.baseline, decision, next_tip, baseline=True),
        inputs=CallInputs(
            projection_snapshot_ids=sheet.projection_snapshot_ids,
            settings_version=sheet.settings_version,
        ),
        producer=ENGINE_PRODUCER,
        outcome_window=OutcomeWindow(start=next_tip, end=week_over),
        first_tip_utc=next_tip,
        detail=_detail(sheet, starter, decision),
    )


def _claim(
    starter: StarterWeek,
    action: Action,
    decision: LockDecision,
    deadline: datetime,
    *,
    baseline: bool = False,
) -> Claim:
    score = f"{decision.score:.1f}"
    if baseline:
        text = (
            f"{'Lock' if action is Action.LOCK else 'Pass on'} {starter.name}'s {score}: "
            "the simple rule (lock if it beats his next game's projection)"
        )
    elif action is Action.LOCK:
        text = f"Lock {starter.name}'s {score}: waiting is worth {decision.value_of_waiting:.1f}"
    else:
        text = f"Pass on {starter.name}'s {score}: waiting is worth {decision.value_of_waiting:.1f}"
    return Claim(
        text=text, moves=(Move(action=action, player_id=starter.player_id),), act_by=deadline
    )


def _detail(sheet: LockInSheet, starter: StarterWeek, decision: LockDecision) -> dict[str, Any]:
    latest = _latest(starter)
    return {
        "week": sheet.week,
        "slot": starter.slot,
        "player": starter.name,
        "latest_game_date": latest.game_date.isoformat(),
        "score": round(decision.score, 2),
        "value_of_waiting": round(decision.value_of_waiting, 2),
        "margin": round(decision.margin, 2),
        "near_tie": decision.near_tie,
        "games_left": decision.games_left,
        "sleeper_shows": starter.sleeper_shows.value,
        "reported_points": starter.reported_points,
        "upcoming": [
            {
                "date": g.game_date.isoformat(),
                "tip_utc": g.tip_utc.isoformat(),
                "mean": round(g.distribution.mean, 2),
                "sd": round(g.distribution.sd, 2),
                "play_probability": g.distribution.play_probability,
                "source": g.source.value,
            }
            for g in starter.upcoming
        ],
        "prior_cv": round(sheet.prior_cv, 3),
    }
