"""One game's fantasy points as a distribution, in a league's own scoring. Pure functions.

v1 (M2): the mean is the provider's projection for that game, scored by the league's rules,
plus the expected value of the league's threshold bonuses. The spread comes from the player's own
box scores this season, shrunk toward a league-wide prior so a player with three games doesn't
get a spread from three numbers. A game the provider expects him to sit is a 0.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from fantasy_helper.domain.league import ScoringRules
from fantasy_helper.domain.stats import Stat, StatLine
from fantasy_helper.engine.scoring import projected_points

PRIOR_GAMES = 5.0  # how many games' worth of weight the prior spread gets
DEFAULT_CV = 0.32  # spread / mean of a game's fantasy points, used until there's data
MIN_PRIOR_SAMPLE = 30  # players with 5+ games needed before the league's own prior is trusted


@dataclass(frozen=True)
class GameDistribution:
    """Fantasy points for one game: with `play_probability` he plays and scores about
    Normal(mean, sd); otherwise he scores 0."""

    mean: float
    sd: float
    play_probability: float = 1.0

    @property
    def expected(self) -> float:
        return self.play_probability * self.mean


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def sample_sd(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def shrunk_sd(
    history: Sequence[float], mean: float, prior_cv: float, prior_games: float = PRIOR_GAMES
) -> float:
    """The player's spread, pulled toward prior_cv * mean: with n games, the data has weight n
    and the prior has weight `prior_games`."""
    prior_var = (prior_cv * max(mean, 0.0)) ** 2
    observed = sample_sd(history)
    if observed is None:
        return math.sqrt(prior_var)
    n = len(history)
    return math.sqrt((n * observed**2 + prior_games * prior_var) / (n + prior_games))


def league_cv(
    histories: Sequence[Sequence[float]], min_games: int = 5, min_mean: float = 20.0
) -> float:
    """The typical spread-to-mean ratio of game scores across rostered-caliber players (mean of
    at least `min_mean` points, 5+ games), as the median, so a few erratic players don't set it.
    Bench players swing far more relative to their small averages (median 0.47 against 0.385
    for 20+ on 2026-11-06), so they'd overstate a starter's spread. DEFAULT_CV until there's
    enough data."""
    ratios = []
    for games in histories:
        if len(games) >= min_games:
            mean = sum(games) / len(games)
            sd = sample_sd(games)
            if mean >= min_mean and sd is not None:
                ratios.append(sd / mean)
    if len(ratios) < MIN_PRIOR_SAMPLE:
        return DEFAULT_CV
    ratios.sort()
    middle = len(ratios) // 2
    return ratios[middle] if len(ratios) % 2 else (ratios[middle - 1] + ratios[middle]) / 2


def expected_bonus(
    projection: StatLine, scoring: ScoringRules, stat_sds: dict[Stat, float]
) -> float:
    """Each of the league's bonuses times the chance this game reaches its threshold, with the
    stat about Normal(projected, its spread). No bonuses in the league: 0."""
    total = 0.0
    for bonus in scoring.bonuses:
        mean = projection.get(bonus.stat, 0.0)
        sd = stat_sds.get(bonus.stat, 0.0)
        if sd <= 0:
            reached = 1.0 if mean >= bonus.threshold else 0.0
        else:
            reached = 1.0 - normal_cdf((bonus.threshold - mean) / sd)
        total += bonus.points * reached
    return total


def game_distribution(
    projection: StatLine | None,
    scoring: ScoringRules,
    *,
    history: Sequence[float],
    stat_sds: dict[Stat, float],
    prior_cv: float,
) -> GameDistribution:
    """One upcoming game. `projection` is the provider's per-game stat line; an empty one means
    the provider doesn't expect him to play."""
    if not projection:
        return GameDistribution(mean=0.0, sd=0.0, play_probability=0.0)
    mean = projected_points(projection, scoring) + expected_bonus(projection, scoring, stat_sds)
    return GameDistribution(mean=mean, sd=shrunk_sd(history, mean, prior_cv))
