"""Checking our scores against the platforms' own numbers. Pure functions.

Our box scores are the one truth for every league (ADR 0022); the platforms' reported points are
how we check that truth, and our scoring of it, every day:
- season totals (ESPN): our sum of a player's game points equals the platform's season total
- one game a week (lock-in leagues, completed weeks only): the week's number equals our score
  for one of his games that week (the locked game, or the last)
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

TOLERANCE = 0.01


@dataclass(frozen=True)
class Agreement:
    matched: int
    total: int
    gaps: tuple[float, ...] = ()  # how far off each mismatch was (ours minus theirs, or nearest)

    @property
    def rate(self) -> float | None:
        return self.matched / self.total if self.total else None

    def __add__(self, other: "Agreement") -> "Agreement":
        return Agreement(self.matched + other.matched, self.total + other.total,
                         self.gaps + other.gaps)  # fmt: skip


def season_totals(ours: Mapping[str, float], reported: Mapping[str, float]) -> Agreement:
    """Players in both maps: does our season total equal theirs?"""
    keys = [k for k in reported if k in ours]
    gaps = tuple(round(ours[k] - reported[k], 2) for k in keys
                 if abs(ours[k] - reported[k]) >= TOLERANCE)  # fmt: skip
    return Agreement(len(keys) - len(gaps), len(keys), gaps)


def one_game_a_week(
    reported: Iterable[tuple[str, float]], games: Mapping[str, Sequence[float]]
) -> Agreement:
    """(player, the week's reported points) against each player's game scores that week. A
    player with no games that week isn't compared."""
    matched, total, gaps = 0, 0, []
    for key, points in reported:
        scores = games.get(key) or ()
        if not scores:
            continue
        total += 1
        nearest = min(scores, key=lambda s: abs(s - points))
        if abs(nearest - points) < TOLERANCE:
            matched += 1
        else:
            gaps.append(round(nearest - points, 2))
    return Agreement(matched, total, tuple(gaps))
