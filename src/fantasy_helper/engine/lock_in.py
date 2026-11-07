"""Lock or pass: optimal stopping for lock-in leagues. Pure functions.

In a lock-in league each player counts one game a week. After a game you either lock its score
or pass; if you never lock, his last game of the week counts, even one he sits out. So after
last night's game, compare its score with the value of waiting, computed backward:

    V(last game)  = E[X_last]
    V(game k)     = E[max(X_k, V(k + 1))]     (you'd lock game k only if it beats waiting)

Lock if last night's score >= V(next game). The margin says how close the call is; the
confidence is how often the call turns out right when the rest of the week is played out many
times under the same policy (a seeded simulation, so it's repeatable).
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass

from fantasy_helper.domain.recommendations import Action
from fantasy_helper.engine.distributions import GameDistribution, normal_cdf, normal_pdf

NEAR_TIE_POINTS = 2.0  # closer than this, the card says it's close (box score feeds differ by ~2)
SIMULATIONS = 4000


def expected_max(game: GameDistribution, floor: float) -> float:
    """E[max(X, floor)] for one game: 0 if he sits, else Normal(mean, sd)."""
    if game.sd <= 0:
        played = max(game.mean, floor)
    else:
        z = (game.mean - floor) / game.sd
        played = floor + (game.mean - floor) * normal_cdf(z) + game.sd * normal_pdf(z)
    return game.play_probability * played + (1 - game.play_probability) * max(0.0, floor)


def continuation_values(games: Sequence[GameDistribution]) -> list[float]:
    """V(k) for each remaining game, in order. The last one is just its expected value."""
    if not games:
        return []
    values = [games[-1].expected]
    for game in reversed(games[:-1]):
        values.append(expected_max(game, values[-1]))
    return list(reversed(values))


def value_of_waiting(games: Sequence[GameDistribution]) -> float:
    """What passing is worth: the expected score of playing the rest of the week optimally."""
    values = continuation_values(games)
    return values[0] if values else 0.0


@dataclass(frozen=True)
class LockDecision:
    action: Action  # LOCK or PASS
    score: float  # last night's points
    value_of_waiting: float
    margin: float  # score - value_of_waiting: positive favors locking
    confidence: float  # how often this call beats the alternative, by simulation
    baseline: Action  # the simple rule: lock if last night beat his next game's projection
    games_left: int

    @property
    def near_tie(self) -> bool:
        return abs(self.margin) < NEAR_TIE_POINTS

    @property
    def expected_value(self) -> float:
        """The engine's expected points for the call it makes."""
        return self.score if self.action is Action.LOCK else self.value_of_waiting


def simulate_waiting(
    games: Sequence[GameDistribution], *, runs: int = SIMULATIONS, seed: int = 0
) -> list[float]:
    """The week played out `runs` times under the optimal policy: lock game k when it beats
    V(k + 1); otherwise go on to the last game, which counts."""
    rng = random.Random(seed)  # noqa: S311 (a repeatable simulation, not security)
    values = continuation_values(games)
    outcomes = []
    for _ in range(runs):
        result = 0.0
        for k, game in enumerate(games):
            played = rng.random() < game.play_probability
            x = rng.gauss(game.mean, game.sd) if played and game.sd > 0 else (
                game.mean if played else 0.0
            )  # fmt: skip
            if k == len(games) - 1 or x >= values[k + 1]:
                result = x
                break
        outcomes.append(result)
    return outcomes


def decide(
    score: float, remaining: Sequence[GameDistribution], *, seed: int = 0
) -> LockDecision | None:
    """Lock or pass on last night's `score`. None when no games are left this week: the last
    game counts on its own, so there's nothing to decide."""
    if not remaining:
        return None
    wait = value_of_waiting(remaining)
    action = Action.LOCK if score >= wait else Action.PASS
    outcomes = simulate_waiting(remaining, seed=seed)
    if action is Action.LOCK:
        right = sum(1 for o in outcomes if o <= score)
    else:
        right = sum(1 for o in outcomes if o > score)
    baseline = Action.LOCK if score >= remaining[0].expected else Action.PASS
    return LockDecision(
        action=action,
        score=score,
        value_of_waiting=wait,
        margin=score - wait,
        confidence=right / len(outcomes),
        baseline=baseline,
        games_left=len(remaining),
    )


def week_value(
    games: Sequence[GameDistribution], *, latest: float | None = None, seed: int = 0
) -> tuple[float, float]:
    """A lock-in player's week: the expected score of the game that counts under optimal
    locking, and its variance (from the same seeded simulation). `latest` is a finished game
    he can still lock: then it's E[max(latest, the rest played optimally)]."""
    if not games:
        return (latest, 0.0) if latest is not None else (0.0, 0.0)
    outcomes = simulate_waiting(games, seed=seed)
    if latest is not None:
        outcomes = [max(latest, o) for o in outcomes]
    mean = sum(outcomes) / len(outcomes)
    return mean, sum((o - mean) ** 2 for o in outcomes) / len(outcomes)
