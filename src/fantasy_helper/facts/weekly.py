"""Facts for the weekly overview: one sheet per league, for the rest of the matchup week.

The week is dated: the matchup week's days (Monday to Sunday, week 1 from opening night), and
only games that haven't tipped yet. The weekly run happens before the day's games, so on a
Monday morning that's the whole week; run mid-week, it's what's left.

Mid-week, points already banked come from our box scores (one truth, ADR 0022) times who was in
a starting slot each finished day (ESPN's lineup for that day, stored by ingest). ESPN's own
daily totals are read only to check that figure. Lock-in leagues have no banked figure: it
depends on locks the app can't see.

Per game, the projection is the provider's projection for that game when there is one (Sleeper's
per-game rows, RotoWire's numbers), then a per-game season average (Sleeper, then ESPN); each is
scored by this league's rules and given a spread from the player's own box scores.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.adapters.espn.mapper import scoring_period_of
from fantasy_helper.domain.calendar import NBA_TIMEZONE, matchup_week_bounds, matchup_week_of
from fantasy_helper.domain.league import (
    AcquisitionLimits,
    League,
    LeagueRules,
    LineupMode,
    Platform,
)
from fantasy_helper.domain.player import Position
from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import GameDistribution, game_distribution, league_cv
from fantasy_helper.engine.schedule import PlayerWeek
from fantasy_helper.engine.scoring import game_points
from fantasy_helper.facts.availability import season_fallback, sitting
from fantasy_helper.facts.lock_in import MissingFactsError
from fantasy_helper.storage.repositories import league_data, nba, players
from fantasy_helper.storage.tables import PlayerGameStatRow, stat_line

FREE_AGENT = "FA"
IR = "IR"
BENCHED = frozenset({"BENCH", "IR"})


@dataclass(frozen=True)
class WeekPlayer:
    player_id: UUID
    name: str
    nba_team: str | None
    positions: tuple[Position, ...]
    slot: str  # roster slot, or "FA" for a free agent
    week: PlayerWeek  # his games left this week, with distributions
    season_per_game: float | None = None  # his average this season (3+ games), league scoring
    # Lock-in leagues: his latest game's score while it can still be locked (his next game
    # hasn't tipped). None otherwise.
    lockable_points: float | None = None

    @property
    def expected(self) -> float:
        return sum(d.expected for _, d in self.week.games)

    @property
    def per_game(self) -> float:
        return self.expected / len(self.week.games) if self.week.games else 0.0


@dataclass(frozen=True)
class TeamWeek:
    team_external_id: str
    name: str
    players: tuple[WeekPlayer, ...]

    @property
    def active(self) -> list[WeekPlayer]:
        """Everyone who can be started: not on IR."""
        return [p for p in self.players if p.slot != IR]


@dataclass(frozen=True)
class WeeklySheet:
    league: League
    rules: LeagueRules
    settings_version: int
    week: int
    week_start: date
    week_end: date
    from_day: date  # the first day counted (today, or the week's first day)
    as_of: datetime
    mine: TeamWeek
    opponent: TeamWeek | None
    free_agents: tuple[WeekPlayer, ...]
    adds_left: int | None  # None: no limit
    projection_snapshot_ids: tuple[UUID, ...]
    first_tip_utc: datetime | None  # the earliest game left this week, for call deadlines
    # Points banked this week before today, from our box scores and each day's lineup. None when
    # unknown (a lock-in league, or a finished game day without a stored lineup).
    my_banked: float | None = None
    their_banked: float | None = None
    my_banked_reported: float | None = None  # ESPN's own daily totals, summed: a check only
    # ESPN's matchup for this week began in an earlier calendar week (a multi-week period, like
    # the All-Star break): the opponent and banked points can't be read by calendar week, so
    # the matchup forecast is skipped rather than guessed.
    period_mismatch: bool = False


def load_weekly(
    session: Session,
    league: League,
    *,
    now: datetime,
    box_scores: Sequence[PlayerGameStatRow] | None = None,
) -> WeeklySheet:
    """`box_scores` lets a run load the season's box scores once for every league."""
    rules_row = league_data.latest_rules(session, league.id)
    opening = nba.season_start(session, league.season)
    if rules_row is None or opening is None or league.my_team_external_id is None:
        raise MissingFactsError("settings, schedule or my team aren't loaded yet")
    version, rules = rules_row
    today = now.astimezone(NBA_TIMEZONE).date()
    week = matchup_week_of(opening, today)
    week_start, week_end = matchup_week_bounds(opening, week)
    from_day = max(week_start, today)

    # Games still counting: today's (even if they've tipped: their box scores aren't in yet, so
    # they're projected) and every later one this week. Banked points cover the days before.
    games_by_team: dict[str, list[tuple[datetime, date]]] = defaultdict(list)
    team_week: dict[str, list[tuple[datetime, date]]] = defaultdict(list)
    future_tips: list[datetime] = []
    for _, tip, day, home, away in nba.games_between(session, league.season, week_start, week_end):
        for team_code in (home, away):
            team_week[team_code].append((tip, day))
            if day >= from_day:
                games_by_team[team_code].append((tip, day))
        if day >= from_day and tip > now:
            future_tips.append(tip)
    lock_in = rules.lineup_mode is LineupMode.LOCK_IN

    rosters = league_data.latest_rosters(session, league.id)
    rostered = {pid for _, pid, _ in rosters}
    snapshots = _snapshots(session, week, now)
    pool = players.with_external_id(session, league.platform.value)
    candidates = nba.projected_players(session, [s for s in snapshots.values() if s])
    fa_ids = [pid for pid in candidates if pid in pool and pid not in rostered]
    ids = list(rostered) + fa_ids
    profiles = players.profiles(session, ids)
    book = _ProjectionBook(session, snapshots, ids, sitting(session, ids))
    history, prior_cv, by_day = _scored_history(session, league, rules, box_scores)

    def week_player(pid: UUID, slot: str) -> WeekPlayer | None:
        if pid not in profiles:
            return None
        name, team, positions = profiles[pid]
        games = []
        for tip, day in sorted(games_by_team.get(team or "", [])):
            if slot == FREE_AGENT and tip <= now:
                continue  # a free agent can't be added in time for a game that's started
            projection = book.for_game(pid, day)
            if projection is None:
                continue
            if not rules.scoring.weights:  # categories: a game counts as a game
                dist = GameDistribution(1.0, 0.0, 1.0 if projection else 0.0)
            else:
                dist = game_distribution(
                    projection, rules.scoring, history=history.get(pid, []),
                    stat_sds={}, prior_cv=prior_cv,
                )  # fmt: skip
            games.append((day, dist))
        pos = tuple(Position(p) for p in positions)
        played = history.get(pid, [])
        season = sum(played) / len(played) if len(played) >= 3 else None
        lockable = (
            _lockable(pid, team_week.get(team or "", []), by_day, week_start, today, now)
            if lock_in
            else None
        )
        return WeekPlayer(
            pid, name, team, pos, slot, PlayerWeek(pid, pos, tuple(games)), season, lockable
        )

    names = league_data.team_names(session, league.id)

    def team(team_id: str) -> TeamWeek:
        members = [week_player(pid, slot) for t, pid, slot in rosters if t == team_id]
        name = names.get(team_id, team_id).strip()
        return TeamWeek(team_id, name, tuple(p for p in members if p))

    opp_id = league_data.opponent(session, league.id, week, league.my_team_external_id)
    free_agents = tuple(
        p
        for p in (week_player(pid, FREE_AGENT) for pid in fa_ids)
        if p is not None and p.week.games
    )
    my_banked = their_banked = reported = None
    mismatch = False
    if league.platform is Platform.ESPN:
        reported_by_day = league_data.reported_team_points(
            session, league.id, week, league.my_team_external_id
        )
        mismatch = period_started_before(reported_by_day, scoring_period_of(week_start, opening))
        if mismatch:
            opp_id = None
    if not mismatch and rules.lineup_mode is LineupMode.DAILY and rules.scoring.weights:
        days = _game_days(session, league.season, week_start, today)
        my_banked = _banked(session, league.id, league.my_team_external_id, days, by_day)
        if opp_id:
            their_banked = _banked(session, league.id, opp_id, days, by_day)
        if league.platform is Platform.ESPN:
            reported = sum(reported_by_day.get(scoring_period_of(d, opening), 0.0) for d in days)
    return WeeklySheet(
        league=league, rules=rules, settings_version=version, week=week,
        week_start=week_start, week_end=week_end, from_day=from_day, as_of=now,
        mine=team(league.my_team_external_id),
        opponent=team(opp_id) if opp_id else None,
        free_agents=free_agents,
        adds_left=_adds_left(session, league, rules, week_start, week_end),
        projection_snapshot_ids=tuple(s for s in snapshots.values() if s),
        first_tip_utc=min(future_tips) if future_tips else None,
        my_banked=my_banked, their_banked=their_banked, my_banked_reported=reported,
        period_mismatch=mismatch,
    )  # fmt: skip


def _snapshots(session: Session, week: int, now: datetime) -> dict[str, UUID | None]:
    return {
        "sleeper_game": nba.latest_projection_snapshot(
            session, Provider.SLEEPER, ProjectionScope.WEEK, str(week), now
        ),
        "sleeper_season": nba.latest_projection_snapshot(
            session, Provider.SLEEPER, ProjectionScope.SEASON, None, now
        ),
        "espn_season": nba.latest_projection_snapshot(
            session, Provider.ESPN, ProjectionScope.SEASON, None, now
        ),
    }


class _ProjectionBook:
    """Per-game stat lines, best source first."""

    def __init__(
        self,
        session: Session,
        snapshots: dict[str, UUID | None],
        ids: Sequence[UUID],
        out: set[UUID],
    ) -> None:
        self.out = out
        self.by_game: dict[UUID, dict[date, dict[Stat, float]]] = defaultdict(dict)
        self.season: dict[UUID, dict[Stat, float]] = {}
        if snapshots["sleeper_game"]:
            for row in nba.projection_rows(session, snapshots["sleeper_game"], ids):
                if row.game_date is not None:
                    # An empty per-game row (0 games) means "not expected to play".
                    self.by_game[row.player_id][row.game_date] = (
                        {} if row.games == 0 else stat_line(row)
                    )
        for key in ("espn_season", "sleeper_season"):  # Sleeper overwrites ESPN where both exist
            if snapshots[key]:
                for row in nba.projection_rows(session, snapshots[key], ids):  # type: ignore[arg-type]
                    self.season[row.player_id] = stat_line(row)

    def for_game(self, player_id: UUID, day: date) -> dict[Stat, float] | None:
        if day in self.by_game.get(player_id, {}):
            return self.by_game[player_id][day]
        return season_fallback(self.season.get(player_id), player_id, self.out)


def _scored_history(
    session: Session,
    league: League,
    rules: LeagueRules,
    rows: Sequence[PlayerGameStatRow] | None = None,
) -> tuple[dict[UUID, list[float]], float, dict[tuple[UUID, date], float]]:
    """Each player's played games this season in this league's scoring, the league's spread
    prior, and each player's points by game day."""
    history: dict[UUID, list[float]] = defaultdict(list)
    by_day: dict[tuple[UUID, date], float] = {}
    if rules.scoring.weights:
        for row in rows if rows is not None else nba.box_scores(session, league.season):
            if row.played:
                points = game_points(stat_line(row), rules.scoring)
                history[row.player_id].append(points)
                by_day[(row.player_id, row.game_date)] = points
    return history, league_cv(list(history.values())), by_day


def _game_days(session: Session, season: int, week_start: date, today: date) -> list[date]:
    """Days of this week before today with at least one NBA game (election day had none)."""
    if today <= week_start:
        return []
    last = date.fromordinal(today.toordinal() - 1)
    return sorted({day for _, _, day, _, _ in nba.games_between(session, season, week_start, last)})


def _banked(
    session: Session,
    league_id: UUID,
    team_id: str,
    days: Sequence[date],
    by_day: dict[tuple[UUID, date], float],
) -> float | None:
    """A team's points on finished days: each day's starters times their box score points."""
    total = 0.0
    for day in days:
        lineup = [(pid, slot) for team, pid, slot in league_data.roster_on(session, league_id, day)
                  if team == team_id]  # fmt: skip
        if not lineup:
            return None  # no lineup stored for a game day: unknown, not zero
        total += sum(by_day.get((pid, day), 0.0) for pid, slot in lineup if slot not in BENCHED)
    return round(total, 2)


def matchup_add_limit(limits: AcquisitionLimits, days: int) -> int | None:
    """Adds allowed in a matchup of `days` days: a per-day rate scales with its length (week 1
    runs Tuesday to Sunday; some weeks are longer). None: no limit."""
    if limits.per_matchup_per_day is not None:
        return round(limits.per_matchup_per_day * days)
    return limits.per_matchup


def period_started_before(reported_by_period: dict[int, float], first_period: int) -> bool:
    """ESPN's matchup has points from before this calendar week: it spans several weeks."""
    return any(period < first_period for period in reported_by_period)


def _lockable(
    player_id: UUID,
    team_games: Sequence[tuple[datetime, date]],
    by_day: dict[tuple[UUID, date], float],
    week_start: date,
    today: date,
    now: datetime,
) -> float | None:
    """His latest game this week before today, if it can still be locked: his team's next game
    hasn't tipped. (A game he sat out tipped in between closes the window too.)"""
    played = sorted(day for (pid, day) in by_day if pid == player_id and week_start <= day < today)
    if not played:
        return None
    latest = played[-1]
    later = sorted(tip for tip, day in team_games if day > latest)
    if later and later[0] <= now:
        return None
    return by_day[(player_id, latest)]


def _adds_left(
    session: Session, league: League, rules: LeagueRules, week_start: date, week_end: date
) -> int | None:
    allowed = matchup_add_limit(rules.acquisition_limits, (week_end - week_start).days + 1)
    if allowed is None:
        return None
    since = datetime.combine(week_start, time(0), NBA_TIMEZONE)
    used = league_data.adds_since(session, league.id, league.my_team_external_id or "", since)
    return max(allowed - used, 0)


def expected_week(players: Sequence[WeekPlayer]) -> list[PlayerWeek]:
    return [p.week for p in players]


def is_points_league(sheet: WeeklySheet) -> bool:
    return bool(sheet.rules.scoring.weights)


def distribution_for_count(week: PlayerWeek) -> PlayerWeek:
    """Categories leagues: every game counts as 1, so lineups maximize games started."""
    games = tuple((d, GameDistribution(1.0, 0.0, g.play_probability)) for d, g in week.games)
    return PlayerWeek(week.player_id, week.positions, games)
