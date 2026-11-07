"""Streaming: add/drop moves that put more games in starting slots this week. Pure functions.

Greedy and day-aware: a free agent is worth the expected points his games add to the week's
started lineup, after dropping the rostered player whose loss costs least. Moves are picked one
at a time, best first, up to the adds left in the league's limit, re-scoring after each.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from fantasy_helper.domain.league import RosterSlot
from fantasy_helper.engine.schedule import PlayerWeek, project_week


@dataclass(frozen=True)
class StreamMove:
    add: UUID
    drop: UUID
    gain: float  # expected points added to this week's started lineup
    games_added: int  # started games gained


def plan_streams(
    roster: Sequence[PlayerWeek],
    free_agents: Sequence[PlayerWeek],
    slots: Sequence[RosterSlot],
    *,
    adds_left: int,
    keep: frozenset[UUID] = frozenset(),
    min_gain: float = 1.0,
    shortlist: int = 40,
) -> list[StreamMove]:
    """Up to `adds_left` moves, each adding at least `min_gain` expected points. Players in
    `keep` (say, a star resting this week) are never suggested as drops. Only the `shortlist`
    free agents with the most expected points this week are tried, which keeps it quick."""
    current = list(roster)
    pool = sorted(free_agents, key=lambda p: -sum(d.expected for _, d in p.games))[:shortlist]
    moves: list[StreamMove] = []
    for _ in range(max(adds_left, 0)):
        base = project_week(current, slots)
        best: StreamMove | None = None
        for drop in current:
            if drop.player_id in keep:
                continue
            others = [p for p in current if p.player_id != drop.player_id]
            for fa in pool:
                week = project_week([*others, fa], slots)
                gain = week.points - base.points
                if best is None or gain > best.gain:
                    added = week.startable - base.startable
                    best = StreamMove(fa.player_id, drop.player_id, gain, added)
        if best is None or best.gain < min_gain:
            break
        moves.append(best)
        current = [p for p in current if p.player_id != best.drop]
        current.append(next(fa for fa in pool if fa.player_id == best.add))
        pool = [fa for fa in pool if fa.player_id != best.add]
    return moves
