"""Games and startable games in a matchup week. Pure functions.

A player's game only scores if he's in a starting slot that day. Each day, the players with a
game are placed into the league's starting slots, best expected points first, each into the most
specific open slot he fits (a center into C before UTIL), so flexible slots stay open for others.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fantasy_helper.domain.league import RosterSlot
from fantasy_helper.domain.player import Position
from fantasy_helper.engine.distributions import GameDistribution


@dataclass(frozen=True)
class PlayerWeek:
    player_id: UUID
    positions: tuple[Position, ...]
    games: tuple[tuple[date, GameDistribution], ...]  # one per game day, in date order


@dataclass(frozen=True)
class WeekProjection:
    started: dict[UUID, tuple[date, ...]]  # the days each player would be started
    points: float  # expected points from started games
    variance: float  # sum of started games' variances (games treated as independent)
    games: int  # games on the roster this week
    startable: int  # games that fit in a starting slot

    def started_games(self, player_id: UUID) -> int:
        return len(self.started.get(player_id, ()))


def starting_slots(slots: Sequence[RosterSlot]) -> list[tuple[Position, ...]]:
    """One entry per starting slot, most specific first (fewest eligible positions)."""
    expanded = [s.eligible_positions for s in slots if s.starting for _ in range(s.count)]
    return sorted(expanded, key=len)


def fill_day(
    candidates: Sequence[tuple[UUID, tuple[Position, ...], float]],
    slots: Sequence[tuple[Position, ...]],
) -> set[UUID]:
    """Who starts on one day: best expected points first, each into the most specific open
    slot he's eligible for."""
    open_slots = list(slots)
    started: set[UUID] = set()
    for player_id, positions, _ in sorted(candidates, key=lambda c: -c[2]):
        for i, eligible in enumerate(open_slots):
            if set(positions) & set(eligible):
                started.add(player_id)
                del open_slots[i]
                break
    return started


def project_week(players: Sequence[PlayerWeek], slots: Sequence[RosterSlot]) -> WeekProjection:
    """Daily lineups: who starts each day, and the expected points and spread that gives."""
    day_slots = starting_slots(slots)
    by_day: dict[date, list[tuple[UUID, tuple[Position, ...], float, GameDistribution]]] = {}
    for p in players:
        for day, dist in p.games:
            by_day.setdefault(day, []).append((p.player_id, p.positions, dist.expected, dist))
    started: dict[UUID, list[date]] = {}
    points = variance = 0.0
    startable = 0
    for day in sorted(by_day):
        entries = by_day[day]
        chosen = fill_day([(pid, pos, ev) for pid, pos, ev, _ in entries], day_slots)
        for pid, _, _, dist in entries:
            if pid in chosen:
                started.setdefault(pid, []).append(day)
                points += dist.expected
                prob = dist.play_probability
                # variance of a 0-or-Normal game: p * (sd^2 + mean^2) - (p * mean)^2
                variance += prob * (dist.sd**2 + dist.mean**2) - (prob * dist.mean) ** 2
                startable += 1
    return WeekProjection(
        started={pid: tuple(days) for pid, days in started.items()},
        points=points,
        variance=variance,
        games=sum(len(pw.games) for pw in players),
        startable=startable,
    )
