"""Matchup win probability: a normal approximation over the sum of started games. Pure."""

import math

from fantasy_helper.engine.distributions import normal_cdf


def win_probability(my_mean: float, my_var: float, their_mean: float, their_var: float) -> float:
    """P(my total > theirs) with both totals about normal and independent."""
    spread = math.sqrt(max(my_var + their_var, 0.0))
    if spread == 0:
        return 1.0 if my_mean > their_mean else 0.0 if my_mean < their_mean else 0.5
    return normal_cdf((my_mean - their_mean) / spread)
