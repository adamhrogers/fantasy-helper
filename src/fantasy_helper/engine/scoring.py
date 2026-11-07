"""League scoring: one game's stats in a league's own fantasy points. Pure functions.

Every league scores the same box score (ADR 0022) with its own rules: a points league scores a
game as Σ stat * weight, plus every threshold bonus in its settings that the game reaches (two
thresholds on one stat both pay). Checked against ESPN's and Sleeper's own numbers in
docs/spikes/m2-scoring-check.md.

A projection is a per-game average, not a game, so `projected_points` returns only the linear
part. Expected bonuses need a distribution of single games (engine.distributions).
"""

from fantasy_helper.domain.league import ScoringRules, ScoringType
from fantasy_helper.domain.stats import Stat, StatLine


class NotAPointsLeagueError(ValueError):
    """Categories leagues are valued differently (P9), never with fantasy points."""


def _require_points(scoring: ScoringRules) -> None:
    if scoring.scoring_type is not ScoringType.POINTS:
        raise NotAPointsLeagueError("fantasy points need a points league's weights")


def linear_points(stats: StatLine, scoring: ScoringRules) -> float:
    """Σ stat * weight over the league's weighted stats. A stat the line lacks counts as 0."""
    _require_points(scoring)
    return sum(weight * stats.get(stat, 0.0) for stat, weight in scoring.weights.items())


def bonus_points(stats: StatLine, scoring: ScoringRules) -> float:
    """Every bonus whose threshold this one game's stat reaches; they stack."""
    _require_points(scoring)
    return sum(b.points for b in scoring.bonuses if stats.get(b.stat, 0.0) >= b.threshold)


def game_points(stats: StatLine, scoring: ScoringRules) -> float:
    """One game's fantasy points in this league. A game a player sat out (no stats) is 0."""
    return linear_points(stats, scoring) + bonus_points(stats, scoring)


def projected_points(per_game: StatLine, scoring: ScoringRules) -> float:
    """A per-game projection in league points, without bonuses (see the module docstring)."""
    return linear_points(per_game, scoring)


def missing_stats(stats: StatLine, scoring: ScoringRules) -> frozenset[Stat]:
    """Weighted or bonus stats the line doesn't carry: their points would silently be 0."""
    _require_points(scoring)
    needed = {s for s, w in scoring.weights.items() if w} | {b.stat for b in scoring.bonuses}
    return frozenset(needed - stats.keys())
