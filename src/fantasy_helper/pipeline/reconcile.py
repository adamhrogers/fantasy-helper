"""The daily check of our box scores and scoring against the platforms' own numbers (ADR 0022).

Settled data only:
- ESPN points leagues: every rostered player's season total, leaving out anyone whose team has a
  game today that's already tipped (its points would be partial on one side).
- Sleeper lock-in leagues: completed weeks only. Mid-week, Sleeper's numbers move during the day.

The thresholds come from the first check (2026-11-06): ESPN matched exactly (155 of 155);
Sleeper 405 of 417, the rest from the two stat feeds crediting one stat differently.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.domain.calendar import NBA_TIMEZONE, matchup_week_bounds, matchup_week_of
from fantasy_helper.domain.league import League, LineupMode, Platform
from fantasy_helper.engine.reconcile import Agreement, one_game_a_week, season_totals
from fantasy_helper.engine.scoring import game_points
from fantasy_helper.pipeline.ingest import IngestReport
from fantasy_helper.storage.repositories import league_data, nba, players
from fantasy_helper.storage.tables import PlayerGameStatRow, stat_line

SEASON_TOTALS_MIN = 0.98
ONE_GAME_A_WEEK_MIN = 0.90


@dataclass(frozen=True)
class LeagueCheck:
    league: League
    method: str  # "season totals" or "one game a week"
    agreement: Agreement
    minimum: float

    @property
    def ok(self) -> bool:
        rate = self.agreement.rate
        return rate is None or rate >= self.minimum


def reconcile(session: Session, ingest: IngestReport, *, now: datetime) -> list[LeagueCheck]:
    today = now.astimezone(NBA_TIMEZONE).date()
    links = players.load_links(session)
    checks: list[LeagueCheck] = []
    rows_by_season: dict[int, list[PlayerGameStatRow]] = {}
    for result in ingest.leagues:
        league = result.league
        if result.failure is not None:
            continue
        found = league_data.latest_rules(session, league.id)
        if found is None or not found[1].scoring.weights:
            continue  # categories leagues have no fantasy points to compare
        rules = found[1]
        rows = rows_by_season.setdefault(league.season, nba.box_scores(session, league.season))
        scored = [(r, game_points(stat_line(r), rules.scoring)) for r in rows]
        if league.platform is Platform.ESPN and result.reported_season_points:
            ours = _espn_totals(session, scored, links, league.season, today, now)
            reported = {k: v for k, v in result.reported_season_points.items() if k in ours}
            checks.append(
                LeagueCheck(
                    league, "season totals", season_totals(ours, reported), SEASON_TOTALS_MIN
                )
            )
        elif league.platform is Platform.SLEEPER and rules.lineup_mode is LineupMode.LOCK_IN:
            agreement = _sleeper_weeks(session, league, scored, links, today)
            checks.append(LeagueCheck(league, "one game a week", agreement, ONE_GAME_A_WEEK_MIN))
    return checks


def _espn_totals(
    session: Session,
    scored: list[tuple[PlayerGameStatRow, float]],
    links: dict,  # type: ignore[type-arg]
    season: int,
    today: date,
    now: datetime,
) -> dict[str, float]:
    """Our season totals by ESPN player ID, for players with no game today that has tipped."""
    tipped = {
        team
        for _, tip, _, home, away in nba.games_between(session, season, today, today)
        if tip <= now
        for team in (home, away)
    }
    by_player: dict[UUID, float] = defaultdict(float)
    for row, points in scored:
        if row.played and row.game_date < today:
            by_player[row.player_id] += points
    teams = players.names_and_teams(session, list(by_player))
    out: dict[str, float] = {}
    for (source, external_id), link in links.items():
        if source != "espn" or link.player_id not in by_player:
            continue
        if teams.get(link.player_id, ("", None))[1] in tipped:
            continue
        out[external_id] = by_player[link.player_id]
    return out


def _sleeper_weeks(
    session: Session,
    league: League,
    scored: list[tuple[PlayerGameStatRow, float]],
    links: dict,  # type: ignore[type-arg]
    today: date,
) -> Agreement:
    opening = nba.season_start(session, league.season)
    if opening is None:
        return Agreement(0, 0)
    by_player: dict[UUID, list[tuple[date, float]]] = defaultdict(list)
    for row, points in scored:  # a game he sat out counts as 0: it can be the week's last game
        by_player[row.player_id].append((row.game_date, points if row.played else 0.0))
    sleeper_to_player = {
        ext: link.player_id for (src, ext), link in links.items() if src == "sleeper"
    }
    total = Agreement(0, 0)
    for week in range(1, matchup_week_of(opening, today)):
        start, end = matchup_week_bounds(opening, week)
        if end >= today:
            break
        reported = league_data.reported_points_by_period(session, league.id, week)
        games = {
            sid: [p for d, p in by_player.get(pid, []) if start <= d <= end]
            for sid, _ in reported
            if (pid := sleeper_to_player.get(sid)) is not None
        }
        total = total + one_game_a_week(reported, games)
    return total
