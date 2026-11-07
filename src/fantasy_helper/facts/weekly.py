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
from fantasy_helper.domain.league import League, LeagueRules, LineupMode
from fantasy_helper.domain.player import Position
from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.stats import Stat
from fantasy_helper.engine.distributions import GameDistribution, game_distribution, league_cv
from fantasy_helper.engine.schedule import PlayerWeek
from fantasy_helper.engine.scoring import game_points
from fantasy_helper.facts.lock_in import MissingFactsError
from fantasy_helper.storage.repositories import league_data, nba, players
from fantasy_helper.storage.tables import stat_line

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


def load_weekly(session: Session, league: League, *, now: datetime) -> WeeklySheet:
    rules_row = league_data.latest_rules(session, league.id)
    opening = nba.season_start(session, league.season)
    if rules_row is None or opening is None or league.my_team_external_id is None:
        raise MissingFactsError("settings, schedule or my team aren't loaded yet")
    version, rules = rules_row
    today = now.astimezone(NBA_TIMEZONE).date()
    week = matchup_week_of(opening, today)
    week_start, week_end = matchup_week_bounds(opening, week)
    from_day = max(week_start, today)

    games_by_team: dict[str, list[tuple[datetime, date]]] = defaultdict(list)
    for _, tip, day, home, away in nba.games_between(session, league.season, from_day, week_end):
        if tip > now:  # a game that's started can't be planned for
            games_by_team[home].append((tip, day))
            games_by_team[away].append((tip, day))

    rosters = league_data.latest_rosters(session, league.id)
    rostered = {pid for _, pid, _ in rosters}
    snapshots = _snapshots(session, week, now)
    pool = players.with_external_id(session, league.platform.value)
    candidates = nba.projected_players(session, [s for s in snapshots.values() if s])
    fa_ids = [pid for pid in candidates if pid in pool and pid not in rostered]
    ids = list(rostered) + fa_ids
    profiles = players.profiles(session, ids)
    book = _ProjectionBook(session, snapshots, ids)
    history, prior_cv, by_day = _scored_history(session, league, rules)

    def week_player(pid: UUID, slot: str) -> WeekPlayer | None:
        if pid not in profiles:
            return None
        name, team, positions = profiles[pid]
        games = []
        for _, day in sorted(games_by_team.get(team or "", [])):
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
        return WeekPlayer(pid, name, team, pos, slot, PlayerWeek(pid, pos, tuple(games)), season)

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
    tips = [tip for games in games_by_team.values() for tip, _ in games]
    my_banked = their_banked = reported = None
    if rules.lineup_mode is LineupMode.DAILY and rules.scoring.weights:
        days = _game_days(session, league.season, week_start, today)
        my_banked = _banked(session, league.id, league.my_team_external_id, days, by_day)
        if opp_id:
            their_banked = _banked(session, league.id, opp_id, days, by_day)
        reported_by_day = league_data.reported_team_points(
            session, league.id, week, league.my_team_external_id
        )
        reported = sum(reported_by_day.get(scoring_period_of(d, opening), 0.0) for d in days)
    return WeeklySheet(
        league=league, rules=rules, settings_version=version, week=week,
        week_start=week_start, week_end=week_end, from_day=from_day, as_of=now,
        mine=team(league.my_team_external_id),
        opponent=team(opp_id) if opp_id else None,
        free_agents=free_agents,
        adds_left=_adds_left(session, league, rules, week_start),
        projection_snapshot_ids=tuple(s for s in snapshots.values() if s),
        first_tip_utc=min(tips) if tips else None,
        my_banked=my_banked, their_banked=their_banked, my_banked_reported=reported,
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
        self, session: Session, snapshots: dict[str, UUID | None], ids: Sequence[UUID]
    ) -> None:
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
        return self.season.get(player_id)


def _scored_history(
    session: Session, league: League, rules: LeagueRules
) -> tuple[dict[UUID, list[float]], float, dict[tuple[UUID, date], float]]:
    """Each player's played games this season in this league's scoring, the league's spread
    prior, and each player's points by game day."""
    history: dict[UUID, list[float]] = defaultdict(list)
    by_day: dict[tuple[UUID, date], float] = {}
    if rules.scoring.weights:
        for row in nba.box_scores(session, league.season):
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


def _adds_left(
    session: Session, league: League, rules: LeagueRules, week_start: date
) -> int | None:
    limits = rules.acquisition_limits
    if limits.per_matchup_per_day is not None:
        allowed = round(limits.per_matchup_per_day * 7)
    elif limits.per_matchup is not None:
        allowed = limits.per_matchup
    else:
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
