"""What happened last night, in a league's own scoring. Pure functions: no I/O, no clock.

Every league scores the same box score (ADR 0022) with its own rules, so one `GameValue` per
league turns a game's stat line into a number:
- a points league: the league's fantasy points, bonuses included (`engine.scoring`)
- a categories league has no points, so a game is valued as an index: for each category the
  league scores, how many standard deviations the game's stat sits from a typical game this
  season (a ratio category like FG% counts makes above what the league's average shooting would
  have made on those attempts), added up, with a reversed category like turnovers counting
  against. It is linear in the stat line, so a projected line scores as the expected game.

On top of that value: who stood out, who beat or missed what was expected of them (the
projection blended with their own games, `engine.distributions`), which recent pickups had a
good night, what a team's lineup scored in a day, and whether those totals agree with the
platform's own (ADR 0022: platform numbers only ever check ours).
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from fantasy_helper.domain.league import (
    NON_STARTING_SLOTS,
    CategoryRule,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.stats import StatLine
from fantasy_helper.engine.distributions import (
    PRIOR_GAMES,
    GameDistribution,
    blended_mean,
    game_distribution,
    sample_sd,
)
from fantasy_helper.engine.reconcile import Agreement
from fantasy_helper.engine.scoring import game_points

SURPRISE_Z = 1.0  # a league-wide surprise: at least this many spreads off the expectation
MY_TEAM_Z = 0.5  # on his own team, where every player matters, a smaller miss is worth saying
GOOD_GAME_QUANTILE = 0.75  # a "good game" beats this share of the season's played games
MIN_GAMES_FOR_GOOD_GAME = 30  # fewer played games than this and the line isn't meaningful yet
DEFAULT_PRIOR_SD = 3.0  # a categories game index's spread, until the league has enough games
MIN_PRIOR_PLAYERS = 30  # players with 5+ games needed before the league's own prior is trusted
DAY_TOLERANCE = 0.5  # our day's points against the platform's: closer than this agrees
CATEGORY_TOLERANCE = 0.5  # a counting category's week-to-date total
RATIO_TOLERANCE = 0.001  # a ratio category (FG%) as a fraction
TIE = 1e-9


# ------------------------------------------------------------------- one game's value


@dataclass(frozen=True)
class CategoryBaseline:
    """A category's typical single game this season: its mean and spread across played games.
    For a ratio category, `ratio` is the league-wide made / attempted, and the stat measured is
    the makes above what that rate would have made on the game's attempts."""

    category: CategoryRule
    mean: float
    sd: float
    ratio: float | None = None


def category_baselines(
    lines: Sequence[StatLine], categories: Iterable[CategoryRule]
) -> tuple[CategoryBaseline, ...]:
    out = []
    for cat in categories:
        if cat.stat is not None:
            xs = [line.get(cat.stat, 0.0) for line in lines]
            ratio = None
        else:
            assert cat.ratio_of is not None  # noqa: S101 (CategoryRule guarantees one kind)
            made, attempted = cat.ratio_of
            attempts = sum(line.get(attempted, 0.0) for line in lines)
            ratio = sum(line.get(made, 0.0) for line in lines) / attempts if attempts else 0.0
            xs = [line.get(made, 0.0) - ratio * line.get(attempted, 0.0) for line in lines]
        mean = sum(xs) / len(xs) if xs else 0.0
        out.append(CategoryBaseline(cat, mean, sample_sd(xs) or 0.0, ratio))
    return tuple(out)


def _measure(baseline: CategoryBaseline, line: StatLine) -> float:
    cat = baseline.category
    if cat.stat is not None:
        return line.get(cat.stat, 0.0)
    assert cat.ratio_of is not None  # noqa: S101
    made, attempted = cat.ratio_of
    return line.get(made, 0.0) - (baseline.ratio or 0.0) * line.get(attempted, 0.0)


@dataclass(frozen=True)
class GameValue:
    """One league's way of valuing a single game (see the module docstring)."""

    scoring: ScoringRules
    baselines: tuple[CategoryBaseline, ...] = ()

    @classmethod
    def for_league(cls, scoring: ScoringRules, lines: Sequence[StatLine]) -> "GameValue":
        """`lines`: the played games that set a categories league's typical game."""
        if scoring.scoring_type is ScoringType.POINTS:
            return cls(scoring)
        return cls(scoring, category_baselines(lines, scoring.categories))

    @property
    def is_points(self) -> bool:
        return self.scoring.scoring_type is ScoringType.POINTS

    @property
    def unit(self) -> str:
        return "points" if self.is_points else "category index"

    def of(self, line: StatLine) -> float:
        if self.is_points:
            return game_points(line, self.scoring)
        total = 0.0
        for b in self.baselines:
            if b.sd <= 0:
                continue
            z = (_measure(b, line) - b.mean) / b.sd
            total += z if b.category.higher_is_better else -z
        return total


def league_sd(histories: Sequence[Sequence[float]], min_games: int = 5) -> float:
    """The typical spread of one player's game values: the median of each player's own, for
    players with `min_games`+. DEFAULT_PRIOR_SD until there are enough players. (The points
    leagues' prior is relative, `engine.distributions.league_cv`; a categories index can sit
    near zero, so its spread is absolute.)"""
    spreads = sorted(
        s for h in histories if len(h) >= min_games and (s := sample_sd(h)) is not None
    )
    if len(spreads) < MIN_PRIOR_PLAYERS:
        return DEFAULT_PRIOR_SD
    middle = len(spreads) // 2
    return spreads[middle] if len(spreads) % 2 else (spreads[middle - 1] + spreads[middle]) / 2


def expected_game(
    projection: StatLine | None,
    value: GameValue,
    *,
    history: Sequence[float],
    prior_cv: float,
    prior_sd: float,
) -> GameDistribution | None:
    """What the engine expected of one player going into a game, in the league's value. None:
    nothing to go on (no projection for him at all). An empty projection is the provider
    saying he isn't expected to play: expected 0, play probability 0.

    Points leagues use the same distribution the weekly overview does. A categories league
    blends the projected line's index with his own games' the same way, with an absolute
    spread shrunk toward the league's."""
    if projection is None:
        return None
    if not projection:
        return GameDistribution(0.0, 0.0, 0.0)
    if value.is_points:
        return game_distribution(
            projection, value.scoring, history=history, stat_sds={}, prior_cv=prior_cv
        )
    mean = blended_mean(value.of(projection), history)
    n = len(history)
    observed = sample_sd(history)
    if observed is None:
        return GameDistribution(mean, prior_sd)
    # The data has weight n and the prior PRIOR_GAMES' worth, as for points.
    return GameDistribution(
        mean, math.sqrt((n * observed**2 + PRIOR_GAMES * prior_sd**2) / (n + PRIOR_GAMES))
    )


# ------------------------------------------------------------------- who did what


@dataclass(frozen=True)
class Performance:
    """One player's night: what he did, valued in the league, and what was expected."""

    player_id: UUID
    name: str
    nba_team: str | None
    played: bool
    value: float  # 0 when he didn't play
    line: StatLine
    expected: GameDistribution | None = None  # None: nothing to compare against
    owner: str | None = None  # the fantasy team's ID in this league; None: a free agent
    slot: str | None = None  # his slot in that team's lineup that day, when it's known

    @property
    def expected_to_play(self) -> bool:
        return self.expected is not None and self.expected.play_probability > 0

    @property
    def gap(self) -> float | None:
        """What he did minus what was expected, for a player who played as expected to."""
        if not self.played or self.expected is None or not self.expected_to_play:
            return None
        return self.value - self.expected.mean

    @property
    def z(self) -> float | None:
        """The gap in his own spreads. None without a spread to measure it by."""
        gap = self.gap
        if gap is None or self.expected is None or self.expected.sd <= 0:
            return None
        return gap / self.expected.sd


def standouts(performances: Iterable[Performance], n: int) -> list[Performance]:
    """The best `n` games of the night, by league value (ties by name, so it's repeatable)."""
    played = [p for p in performances if p.played]
    return sorted(played, key=lambda p: (-p.value, p.name))[:n]


def good_game_line(values: Sequence[float], quantile: float = GOOD_GAME_QUANTILE) -> float | None:
    """The value a game has to reach to count as a good one: better than `quantile` of the
    played games so far. None before there are enough of them to say."""
    if len(values) < MIN_GAMES_FOR_GOOD_GAME:
        return None
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def surprises(
    performances: Iterable[Performance],
    n: int,
    *,
    min_z: float = SURPRISE_Z,
    good_line: float | None = None,
    rostered_only: bool = False,
) -> tuple[list[Performance], list[Performance]]:
    """(better than expected, worse than expected), each the `n` furthest off in their own
    spreads and at least `min_z` off. A free agent only counts as a good surprise when his night
    was a good game (`good_line`) and never as a bad one: a miss only matters on a roster.
    `rostered_only` drops free agents from both lists (his own team has none)."""
    scored = [p for p in performances if p.z is not None]
    beat = [
        p for p in scored
        if (p.z or 0.0) >= min_z
        and (p.owner is not None or (not rostered_only and good_line is not None
                                      and p.value >= good_line))
    ]  # fmt: skip
    missed = [p for p in scored if (p.z or 0.0) <= -min_z and p.owner is not None]
    beat.sort(key=lambda p: (-(p.z or 0.0), p.name))
    missed.sort(key=lambda p: (p.z or 0.0, p.name))
    return beat[:n], missed[:n]


def expected_but_sat(performances: Iterable[Performance]) -> list[Performance]:
    """Rostered players the provider expected to play who didn't (a box score row, no minutes)."""
    return sorted(
        (p for p in performances if not p.played and p.expected_to_play and p.owner is not None),
        key=lambda p: p.name,
    )


@dataclass(frozen=True)
class Pickup:
    """A free-agent or waiver add by a team in the league, and his night."""

    performance: Performance
    team: str  # the team that added him
    added_day: str  # ISO date (Eastern) of the add
    kind: str  # "free_agent" or "waiver"


def pickups_that_did_well(pickups: Iterable[Pickup], line: float | None, n: int) -> list[Pickup]:
    """Pickups whose night was a good game (at the line or above it), best first. None without
    a line yet."""
    if line is None:
        return []
    good = [p for p in pickups if p.performance.played and p.performance.value >= line]
    return sorted(good, key=lambda p: (-p.performance.value, p.performance.name))[:n]


# ------------------------------------------------------------------- a team's day


def lineup_total(lineup: Iterable[tuple[UUID, str]], values: Mapping[UUID, float]) -> float:
    """What a lineup scored in a day: its starters' game values (bench and injured reserve
    don't count). A player with no game that day scores 0."""
    total = sum(values.get(pid, 0.0) for pid, slot in lineup if slot not in NON_STARTING_SLOTS)
    return round(total, 2)


def category_totals(
    lines: Iterable[StatLine], categories: Iterable[CategoryRule]
) -> dict[str, float]:
    """A team's total in each category over some games: counting stats add up, a ratio is the
    team's makes over its attempts (0 with no attempts)."""
    lines = list(lines)
    out: dict[str, float] = {}
    for cat in categories:
        if cat.stat is not None:
            out[cat.name] = sum(line.get(cat.stat, 0.0) for line in lines)
        else:
            assert cat.ratio_of is not None  # noqa: S101
            made, attempted = cat.ratio_of
            attempts = sum(line.get(attempted, 0.0) for line in lines)
            out[cat.name] = (
                sum(line.get(made, 0.0) for line in lines) / attempts if attempts else 0.0
            )
    return out


@dataclass(frozen=True)
class CategoryStanding:
    """Which categories each side leads, by name."""

    leading: tuple[str, ...]
    trailing: tuple[str, ...]
    tied: tuple[str, ...]

    @property
    def record(self) -> str:
        return f"{len(self.leading)}-{len(self.trailing)}-{len(self.tied)}"


def category_standing(
    mine: Mapping[str, float], theirs: Mapping[str, float], categories: Iterable[CategoryRule]
) -> CategoryStanding:
    leading, trailing, tied = [], [], []
    for cat in categories:
        a, b = mine.get(cat.name, 0.0), theirs.get(cat.name, 0.0)
        if abs(a - b) <= TIE:
            tied.append(cat.name)
        elif (a > b) == cat.higher_is_better:
            leading.append(cat.name)
        else:
            trailing.append(cat.name)
    return CategoryStanding(tuple(leading), tuple(trailing), tuple(tied))


# ------------------------------------------------------------------- checking the totals


def compare_totals(
    ours: Mapping[str, float],
    reported: Mapping[str, float],
    *,
    tolerance: float | Mapping[str, float] = DAY_TOLERANCE,
) -> Agreement:
    """Totals in both maps (keys: a side, a category): does ours equal the platform's? A key the
    platform doesn't report isn't compared. `gaps` holds ours minus theirs for each mismatch."""
    keys = [k for k in reported if k in ours]
    gaps = []
    for k in keys:
        # A number (int or float) is one tolerance for every key; a mapping is one per key.
        limit = tolerance.get(k, DAY_TOLERANCE) if isinstance(tolerance, Mapping) else tolerance
        if abs(ours[k] - reported[k]) > limit:
            gaps.append(round(ours[k] - reported[k], 3))
    return Agreement(len(keys) - len(gaps), len(keys), tuple(gaps))
