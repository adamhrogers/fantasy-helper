"""Replacement level and value over replacement, in one league's scoring. Pure functions.

Replacement level at a slot is the best free agent who could fill it in *this* league: a
rostered player is worth what he adds over the player you could pick up instead.
"""

from collections.abc import Sequence

from fantasy_helper.domain.league import RosterSlot
from fantasy_helper.domain.player import Position


def replacement_levels(
    free_agents: Sequence[tuple[tuple[Position, ...], float]], slots: Sequence[RosterSlot]
) -> dict[str, float]:
    """Best free agent's per-game expected points for each starting slot (0 if none fits)."""
    levels = {}
    for slot in slots:
        if not slot.starting:
            continue
        fits = [per_game for positions, per_game in free_agents
                if set(positions) & set(slot.eligible_positions)]  # fmt: skip
        levels[slot.slot] = max(fits, default=0.0)
    return levels


def value_over_replacement(
    per_game: float,
    positions: tuple[Position, ...],
    slots: Sequence[RosterSlot],
    levels: dict[str, float],
) -> float:
    """Per-game points over the free agent who'd replace him: compared at his most replaceable
    eligible slot (the one with the best free agent), so a player is never flattered."""
    eligible = [
        levels[s.slot] for s in slots
        if s.starting and s.slot in levels and set(positions) & set(s.eligible_positions)
    ]  # fmt: skip
    return per_game - max(eligible, default=0.0)
