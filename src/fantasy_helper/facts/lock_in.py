"""Facts for lock-or-pass calls in a lock-in league: one sheet per league per morning.

Everything here is loaded from the database and handed to the engine as plain values:
- the league's rules (its own scoring, ADR 0022)
- my starters, each with his most recent game this week (the one he can still lock, until his
  next game tips off), his earlier games, and his games still to come with their distributions
- what Sleeper's weekly number shows, kept as a diagnostic only. Tested against Adam's actual
  locks on 2026-11-06 it fits no rule (it flips between pulls minutes apart, for locked, open
  and bench players alike), so it's never shown and never a filter: every open decision gets a
  card (docs/spikes/m2-scoring-check.md). Locks have to come from the user.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.domain.calendar import (
    NBA_TIMEZONE,
    sleeper_week_bounds,
    sleeper_week_of,
)
from fantasy_helper.domain.league import NON_STARTING_SLOTS, League, LeagueRules
from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import (
    GameDistribution,
    game_distribution,
    league_cv,
    shrunk_sd,
)
from fantasy_helper.engine.scoring import game_points
from fantasy_helper.storage.repositories import league_data, nba, players
from fantasy_helper.storage.tables import PlayerGameStatRow, stat_line

MATCH_TOLERANCE = 0.01
BONUS_STAT_PRIOR_CV = 0.35


class SleeperShows(StrEnum):
    """Diagnostic only: which of his games Sleeper's weekly number matches. It doesn't show
    locks (tested against real locks, 2026-11-06), so it's never shown and never a filter."""

    LATEST = "latest"  # his most recent game's score
    EARLIER = "earlier"  # an earlier game's score
    NO_MATCH = "no_match"  # no game matches (no matchup data, or the stat feeds differ)


class ProjectionSource(StrEnum):
    GAME = "game"  # the provider's projection for that game
    SEASON = "season"  # the provider's per-game season average
    OWN_AVERAGE = "own_average"  # no projection: his own average this season


@dataclass(frozen=True)
class GameScore:
    game_id: UUID
    game_date: date
    points: float
    played: bool


@dataclass(frozen=True)
class UpcomingGame:
    game_id: UUID
    tip_utc: datetime
    game_date: date
    distribution: GameDistribution
    source: ProjectionSource


@dataclass(frozen=True)
class StarterWeek:
    player_id: UUID
    name: str
    team: str | None
    slot: str
    latest: GameScore | None  # his most recent game this week: lockable until his next tip
    earlier: tuple[GameScore, ...]
    upcoming: tuple[UpcomingGame, ...]
    reported_points: float | None
    sleeper_shows: SleeperShows
    # His next game has tipped (or finished, box score not in yet): the latest game can't be
    # locked any more, and the next decision comes after that game.
    window_closed: bool = False


@dataclass(frozen=True)
class LockInSheet:
    league: League
    rules: LeagueRules
    settings_version: int
    for_date: date  # the morning the calls are for (Eastern time)
    week: int
    week_start: date
    week_end: date
    projection_snapshot_ids: tuple[UUID, ...]
    prior_cv: float
    starters: tuple[StarterWeek, ...]


class MissingFactsError(Exception):
    """Something the sheet needs isn't loaded yet (run `fh ingest`)."""


def load_lock_in(session: Session, league: League, *, now: datetime) -> LockInSheet:
    rules_row = league_data.latest_rules(session, league.id)
    if rules_row is None:
        raise MissingFactsError("no settings for this league yet")
    version, rules = rules_row
    opening = nba.season_start(session, league.season)
    if opening is None:
        raise MissingFactsError("no schedule for this season yet")
    if league.my_team_external_id is None:
        raise MissingFactsError("the league has no team marked as mine")
    today = now.astimezone(NBA_TIMEZONE).date()
    week = sleeper_week_of(opening, today)
    week_start, week_end = sleeper_week_bounds(opening, week)

    lineup = [
        (pid, slot)
        for pid, slot in league_data.latest_roster(session, league.id, league.my_team_external_id)
        if slot not in NON_STARTING_SLOTS
    ]
    ids = [pid for pid, _ in lineup]
    names = players.names_and_teams(session, ids)
    sleeper_ids = players.external_ids(session, ids, "sleeper")
    reported = league_data.reported_player_points(
        session, league.id, week, league.my_team_external_id
    )

    # Everyone's played games, scored by this league: the prior spread comes from all of them.
    all_rows = nba.box_scores(session, league.season)
    scored: dict[UUID, list[tuple[PlayerGameStatRow, float]]] = defaultdict(list)
    for row in all_rows:
        scored[row.player_id].append((row, game_points(stat_line(row), rules.scoring)))
    prior_cv = league_cv([[pts for row, pts in games if row.played] for games in scored.values()])

    game_snapshot = nba.latest_projection_snapshot(
        session, Provider.SLEEPER, ProjectionScope.WEEK, str(week), now
    )
    season_snapshot = nba.latest_projection_snapshot(
        session, Provider.SLEEPER, ProjectionScope.SEASON, None, now
    )
    per_game = _projections(session, game_snapshot, ids, by_date=True)
    season_avg = _projections(session, season_snapshot, ids, by_date=False)

    starters = []
    for pid, slot in lineup:
        name, team = names.get(pid, ("Unknown player", None))
        games = scored.get(pid, [])
        week_games = [
            GameScore(row.game_id, row.game_date, pts, row.played)
            for row, pts in games
            if week_start <= row.game_date < today
        ]
        latest = week_games[-1] if week_games else None  # box scores come in date order
        earlier = tuple(week_games[:-1])
        history = [pts for row, pts in games if row.played]
        after = latest.game_date if latest else week_start - timedelta(days=1)
        upcoming, window_closed = _upcoming(
            session, league, team, after, week_end, now,
            projections_by_date=per_game.get(pid, {}),
            season_projection=season_avg.get(pid, {}).get(None),
            rules=rules, history=history, prior_cv=prior_cv,
            stat_history={  # the stats the league's bonuses are on, game by game
                stat: [stat_line(row).get(stat, 0.0) for row, _ in games if row.played]
                for stat in {b.stat for b in rules.scoring.bonuses}
            },
        )  # fmt: skip
        sleeper_id = sleeper_ids.get(pid)
        points = reported.get(sleeper_id) if reported is not None and sleeper_id else None
        starters.append(
            StarterWeek(
                player_id=pid,
                name=name,
                team=team,
                slot=slot,
                latest=latest,
                earlier=earlier,
                upcoming=upcoming,
                reported_points=points,
                sleeper_shows=sleeper_shows(points, latest, earlier),
                window_closed=window_closed,
            )
        )
    return LockInSheet(
        league=league, rules=rules, settings_version=version, for_date=today, week=week,
        week_start=week_start, week_end=week_end,
        projection_snapshot_ids=tuple(s for s in (game_snapshot, season_snapshot) if s),
        prior_cv=prior_cv, starters=tuple(starters),
    )  # fmt: skip


def sleeper_shows(
    reported: float | None, latest: GameScore | None, earlier: tuple[GameScore, ...]
) -> SleeperShows:
    """Classify Sleeper's weekly number against his games, for diagnostics and later analysis.
    It is not a lock signal (see SleeperShows)."""
    if reported is None or latest is None:
        return SleeperShows.NO_MATCH
    matches_last = abs(reported - latest.points) < MATCH_TOLERANCE
    matches_earlier = any(abs(reported - g.points) < MATCH_TOLERANCE for g in earlier)
    if matches_earlier and not matches_last:
        return SleeperShows.EARLIER
    if matches_last or not earlier:
        return SleeperShows.LATEST
    return SleeperShows.NO_MATCH


def _projections(
    session: Session, snapshot_id: UUID | None, ids: list[UUID], *, by_date: bool
) -> dict[UUID, dict[date | None, dict[Stat, float]]]:
    if snapshot_id is None:
        return {}
    out: dict[UUID, dict[date | None, dict[Stat, float]]] = defaultdict(dict)
    for row in nba.projection_rows(session, snapshot_id, ids):
        key = row.game_date if by_date else None
        # A per-game row the provider left empty (0 games) means "not expected to play".
        out[row.player_id][key] = {} if row.games == 0 else stat_line(row)
    return out


def _upcoming(
    session: Session,
    league: League,
    team: str | None,
    after: date,
    week_end: date,
    now: datetime,
    *,
    projections_by_date: dict[date | None, dict[Stat, float]],
    season_projection: dict[Stat, float] | None,
    rules: LeagueRules,
    history: list[float],
    prior_cv: float,
    stat_history: dict[Stat, list[float]],
) -> tuple[tuple[UpcomingGame, ...], bool]:
    """His team's games after `after` this week, and whether the next one has already tipped
    (which closes the window to lock his latest game)."""
    if team is None:
        return (), False
    games = nba.team_games(session, league.season, team, after + timedelta(days=1), week_end)
    if games and games[0][1] <= now:
        return (), True
    out = []
    for game_id, tip, day in games:
        if day in projections_by_date:
            projection, source = projections_by_date[day], ProjectionSource.GAME
        elif season_projection is not None:
            projection, source = season_projection, ProjectionSource.SEASON
        else:
            projection, source = {}, ProjectionSource.OWN_AVERAGE
        if source is ProjectionSource.OWN_AVERAGE and history:
            mean = sum(history) / len(history)
            dist = GameDistribution(mean=mean, sd=shrunk_sd(history, mean, prior_cv))
        else:
            stat_sds = {
                stat: shrunk_sd(values, projection.get(stat, 0.0), BONUS_STAT_PRIOR_CV)
                for stat, values in stat_history.items()
            }
            dist = game_distribution(
                projection, rules.scoring, history=history, stat_sds=stat_sds, prior_cv=prior_cv
            )
        out.append(UpcomingGame(game_id, tip, day, dist, source))
    return tuple(out), False
