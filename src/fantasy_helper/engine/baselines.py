"""Baseline calls: the simple rule each call type is graded against. Pure functions."""

from collections.abc import Sequence
from uuid import UUID

from fantasy_helper.engine.schedule import PlayerWeek


def top_projected_free_agent(free_agents: Sequence[PlayerWeek]) -> UUID | None:
    """The streaming baseline: the free agent with the best expected points per game, ignoring
    the schedule and the lineup (what a manager sorting the free-agent list by projection does)."""
    best: tuple[float, UUID] | None = None
    for fa in free_agents:
        if not fa.games:
            continue
        per_game = sum(d.expected for _, d in fa.games) / len(fa.games)
        if best is None or per_game > best[0]:
            best = (per_game, fa.player_id)
    return best[1] if best else None
