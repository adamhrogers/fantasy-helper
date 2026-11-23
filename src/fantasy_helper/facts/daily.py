"""Facts for the daily "what happened" cards: last night in one league, in that league's own
scoring (ADR 0022: one box score per player and game, valued by each league's own rules).

For every league, whatever its format:
- every player's night (`Performance`): what he did in league value, what was expected of him
  going in, and who had him for his game (the lineup stored for that day on ESPN; on Sleeper,
  whose stored roster is the morning's, that roster and the day's moves up to his tip)
- the free-agent and waiver pickups of the past week, with their night, counted only when the
  team that added him still had him for that game
- the matchup: last night's points and the matchup so far, for both teams, checked against the
  platform's own totals where it gives them

"Expected" is what the engine expected before the games: the projections that had been pulled
by the first tip-off (and the injury statuses known by then), blended with the player's own
games before last night (`engine.distributions`). Nothing here reads the future.

How the matchup is read depends on the league:
- **Daily or weekly lineups (ESPN):** each day's starters, from the lineup stored for that day,
  times our box scores. ESPN's own daily totals (points leagues) or running category totals
  (categories leagues) only check ours.
- **Lock-in (Sleeper):** each starter counts one game a week, chosen by locking, and the app
  can't see locks (the matchup response has no lock flags). So there is no points total of ours
  to give: last night's games are shown as game scores before locks, next to the platform's own
  score for the week, which stays the platform's number and is never used to score anything.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.adapters.espn.mapper import scoring_period_of
from fantasy_helper.domain.calendar import NBA_TIMEZONE, matchup_week_bounds, matchup_week_of
from fantasy_helper.domain.league import (
    NON_STARTING_SLOTS,
    League,
    LeagueRules,
    LineupMode,
    Platform,
)
from fantasy_helper.domain.stats import StatLine
from fantasy_helper.engine.distributions import league_cv
from fantasy_helper.engine.recap import (
    CATEGORY_TOLERANCE,
    DAY_TOLERANCE,
    RATIO_TOLERANCE,
    GameValue,
    Performance,
    Pickup,
    category_totals,
    compare_totals,
    differing,
    expected_game,
    good_game_line,
    league_sd,
    lineup_total,
)
from fantasy_helper.engine.reconcile import Agreement
from fantasy_helper.facts.availability import sitting
from fantasy_helper.facts.lock_in import MissingFactsError
from fantasy_helper.facts.projections import ProjectionBook, snapshots_as_of
from fantasy_helper.facts.weekly import period_started_before
from fantasy_helper.storage.repositories import league_data, nba, players
from fantasy_helper.storage.tables import PlayerGameStatRow, stat_line

PICKUP_DAYS = 7  # how far back an add still counts as a recent pickup
# Platforms whose stored roster for a finished day is that day's own lineup (ESPN: ingest fetches
# each finished day's lineup). Elsewhere (Sleeper) the stored roster for a day is the one ingested
# that morning, so who had a player at his game is worked out from it and the day's moves.
DAY_LINEUP_PLATFORMS = frozenset({Platform.ESPN})


class MatchupMode(StrEnum):
    POINTS = "points"  # daily or weekly lineups, scored in league points
    CATEGORIES = "categories"  # lineups, scored by category
    LOCK_IN = "lock_in"  # one locked game per starter per week: no total of ours to give


@dataclass(frozen=True)
class Side:
    """One team's side of the matchup."""

    team: str  # the team's ID in the league
    name: str
    # Points leagues: the lineup's points last night, and over the matchup through last night.
    # Lock-in: last night's game scores before locks (the week's total isn't known).
    day_points: float | None = None
    week_points: float | None = None
    # What the platform reports (ESPN's daily totals; Sleeper's score for the week so far). A
    # check on ours, or, for a lock-in league, the only matchup score there is.
    reported_day: float | None = None
    reported_week: float | None = None
    # Categories leagues, by category name.
    day_categories: dict[str, float] = field(default_factory=dict)
    week_categories: dict[str, float] | None = None
    reported_categories: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TotalsDifference:
    """One total where ours and the platform's disagree (beyond the tolerance)."""

    side: str  # "me" or "them"
    what: str  # "day", "week", or a category's name (the week so far)
    ours: float
    theirs: float


@dataclass(frozen=True)
class MatchupDay:
    mode: MatchupMode
    week: int
    final: bool  # last night was the matchup's last day
    me: Side
    them: Side
    checked: Agreement  # our totals against the platform's, both teams: matched out of compared
    differences: tuple[TotalsDifference, ...] = ()  # each total that disagreed, both numbers


@dataclass(frozen=True)
class DailySheet:
    league: League
    rules: LeagueRules
    settings_version: int
    for_date: date  # the morning the report is for (Eastern)
    game_day: date  # last night: the NBA day being reported
    week: int
    value: GameValue
    games: int  # NBA games last night
    performances: tuple[Performance, ...]
    good_line: float | None  # what a game has to reach to count as a good one (None: too early)
    my_team: str
    team_names: dict[str, str]
    lineup_known: bool  # who started last night comes from the lineup stored for that day
    matchup: MatchupDay | None
    pickups: tuple[Pickup, ...]
    projection_snapshot_ids: tuple[UUID, ...]

    @property
    def mine(self) -> list[Performance]:
        return [p for p in self.performances if p.owner == self.my_team]


def load_daily(
    session: Session,
    league: League,
    *,
    now: datetime,
    box_scores: Sequence[PlayerGameStatRow] | None = None,
) -> DailySheet:
    """`box_scores` lets a run load the season's box scores once for every league."""
    rules_row = league_data.latest_rules(session, league.id)
    opening = nba.season_start(session, league.season)
    if rules_row is None or opening is None or league.my_team_external_id is None:
        raise MissingFactsError("settings, schedule or my team aren't loaded yet")
    settings_version, rules = rules_row
    my_team = league.my_team_external_id
    today = now.astimezone(NBA_TIMEZONE).date()
    game_day = today - timedelta(days=1)
    week = matchup_week_of(opening, game_day)
    week_start, week_end = matchup_week_bounds(opening, week)
    names = league_data.team_names(session, league.id)

    rows = box_scores if box_scores is not None else nba.box_scores(session, league.season)
    before = [r for r in rows if r.played and r.game_date < game_day]
    value = GameValue.for_league(rules.scoring, [stat_line(r) for r in before])
    history: dict[UUID, list[float]] = defaultdict(list)
    for r in before:
        history[r.player_id].append(value.of(stat_line(r)))
    all_values = [v for h in history.values() for v in h]
    games = nba.games_between(session, league.season, game_day, game_day)

    performances: list[Performance] = []
    lineup_known = False
    matchup: MatchupDay | None = None
    pickups: tuple[Pickup, ...] = ()
    snapshot_ids: tuple[UUID, ...] = ()
    if games:  # an off night leaves all of these empty: nothing happened
        night = [r for r in rows if r.game_date == game_day]
        ids = [r.player_id for r in night]
        first_tip = min(tip for _, tip, _, _, _ in games)
        snapshots = snapshots_as_of(session, week, first_tip)
        snapshot_ids = tuple(s for s in snapshots.values() if s)
        book = ProjectionBook(session, snapshots, ids, sitting(session, ids, as_of=first_tip))
        profiles = players.profiles(session, ids)
        # Who owned whom comes only from the roster stored for that day. Today's roster is no
        # stand-in: a player traded or dropped since would be credited to the wrong team. With no
        # roster stored, every player is shown without an owner.
        day_roster = league_data.roster_on(session, league.id, game_day)
        lineup_known = bool(day_roster)
        roster: dict[UUID, tuple[str, str | None]] = {
            pid: (team, slot) for team, pid, slot in day_roster
        }
        if lineup_known and league.platform not in DAY_LINEUP_PLATFORMS:
            tips = {gid: tip for gid, tip, _, _, _ in games}
            roster = _at_tip(
                session, league, game_day, roster,
                {r.player_id: tips[r.game_id] for r in night if r.game_id in tips},
            )  # fmt: skip
        prior_cv = league_cv(list(history.values()))
        prior_sd = league_sd(list(history.values()))
        for r in night:
            if r.player_id not in profiles:
                continue
            name, team, _ = profiles[r.player_id]
            line = stat_line(r) if r.played else {}
            owner, slot = roster.get(r.player_id, (None, None))
            performances.append(
                Performance(
                    player_id=r.player_id, name=name, nba_team=r.team or team, played=r.played,
                    value=value.of(line) if r.played else 0.0, line=line,
                    expected=expected_game(
                        book.for_game(r.player_id, game_day), value,
                        history=history.get(r.player_id, []), prior_cv=prior_cv, prior_sd=prior_sd,
                    ),
                    owner=owner, slot=slot,
                )
            )  # fmt: skip
        performances.sort(key=lambda p: (-p.value, p.name))
        matchup = _matchup(
            session, league, rules, rows, value, opening=opening, week=week,
            week_start=week_start, week_end=week_end, game_day=game_day, names=names,
        )  # fmt: skip
        pickups = _pickups(session, league, game_day, performances)

    return DailySheet(
        league=league, rules=rules, settings_version=settings_version, for_date=today,
        game_day=game_day, week=week, value=value, games=len(games),
        performances=tuple(performances), good_line=good_game_line(all_values), my_team=my_team,
        team_names=names, lineup_known=lineup_known, matchup=matchup, pickups=pickups,
        projection_snapshot_ids=snapshot_ids,
    )  # fmt: skip


# ------------------------------------------------------------------------------ pickups


def _pickups(
    session: Session, league: League, game_day: date, performances: Sequence[Performance]
) -> tuple[Pickup, ...]:
    """Adds of the past week whose player played last night for the team that added him.

    A pickup only counts when the adding team had him for his game (his `owner`: the lineup stored
    for the game day, or on Sleeper the morning's roster and the day's moves up to his tip, see
    `_at_tip`). One dropped (or traded) before the game didn't pay off for them. With no roster
    stored for that day, every owner is unknown, so nothing is listed rather than guessed."""
    since = datetime.combine(game_day - timedelta(days=PICKUP_DAYS - 1), time(0), NBA_TIMEZONE)
    # Only adds made by the end of the game day (Eastern): a player added the next morning wasn't
    # a pickup that did well last night.
    until = datetime.combine(game_day + timedelta(days=1), time(0), NBA_TIMEZONE)
    adds = league_data.recent_adds(session, league.id, since, until)
    resolved = _resolve(session, [(add.source, add.external_id) for add in adds])
    night = {p.player_id: p for p in performances}
    latest: dict[UUID, Pickup] = {}
    for add in adds:  # oldest first, so a player added twice keeps his latest add
        pid = resolved.get((add.source, add.external_id))
        if pid is not None and pid in night and night[pid].owner == add.team_external_id:
            added_day = add.proposed_at.astimezone(NBA_TIMEZONE).date()
            latest[pid] = Pickup(night[pid], add.team_external_id, added_day.isoformat(), add.kind)
    return tuple(latest.values())


def _resolve(session: Session, keys: Sequence[tuple[str, str]]) -> dict[tuple[str, str], UUID]:
    """(source, the platform's player ID) to our player, for the ones identity knows."""
    by_source: dict[str, list[str]] = defaultdict(list)
    for source, ext in keys:
        by_source[source].append(ext)
    return {
        (source, ext): pid
        for source, ids in by_source.items()
        for ext, pid in players.resolve_external(session, source, ids).items()
    }


def _at_tip(
    session: Session,
    league: League,
    game_day: date,
    morning: dict[UUID, tuple[str, str | None]],
    tips: dict[UUID, datetime],
) -> dict[UUID, tuple[str, str | None]]:
    """Who had each of the night's players when his own game tipped, for a platform whose stored
    roster for a day is that morning's (Sleeper). Start from the morning's roster, then apply the
    game day's moves that take effect when made (free-agent and commissioner adds and drops) in
    order, each player's only up to his tip. So a player added at noon who played that night is
    the adding team's, and one dropped before his game isn't.

    Waiver claims process overnight, before the morning ingest, so the morning's roster already
    has them. A trade's stored time is when it was proposed, not when it went through, so a trade
    on the game day isn't seen. A move made before the morning ingest is applied again, which
    changes nothing (an add puts him where he already is; a drop only removes him from that
    team). A player's slot is known only while he's where the morning's roster had him."""
    start = datetime.combine(game_day, time(0), NBA_TIMEZONE)
    end = max(tips.values(), default=start)
    moves = league_data.immediate_moves(session, league.id, start, end)
    resolved = _resolve(session, [(m.source, m.external_id) for m in moves])
    owner = {pid: team for pid, (team, _) in morning.items()}
    for move in moves:  # oldest first
        pid = resolved.get((move.source, move.external_id))
        if pid is None or pid not in tips or move.at >= tips[pid]:
            continue
        if move.joined:
            owner[pid] = move.team_external_id
        elif owner.get(pid) == move.team_external_id:
            del owner[pid]
    return {
        pid: (team, morning[pid][1] if morning.get(pid, (None, None))[0] == team else None)
        for pid, team in owner.items()
    }


# ------------------------------------------------------------------------------ the matchup


def _matchup(
    session: Session,
    league: League,
    rules: LeagueRules,
    rows: Sequence[PlayerGameStatRow],
    value: GameValue,
    *,
    opening: date,
    week: int,
    week_start: date,
    week_end: date,
    game_day: date,
    names: dict[str, str],
) -> MatchupDay | None:
    assert league.my_team_external_id is not None  # noqa: S101 (checked by load_daily)
    me = league.my_team_external_id
    opponent = league_data.opponent(session, league.id, week, me)
    if opponent is None:  # a bye, or the matchup isn't loaded
        return None
    if league.platform is Platform.ESPN:
        # A matchup that began before this calendar week (the All-Star break's long period)
        # can't be read by calendar week: skip it, as the weekly overview does.
        by_period = league_data.reported_team_points(session, league.id, week, me)
        if period_started_before(by_period, scoring_period_of(week_start, opening)):
            return None
    final = game_day == week_end
    if rules.lineup_mode is LineupMode.LOCK_IN:
        return _lock_in_matchup(
            session, league, rows, value, week=week, game_day=game_day, final=final,
            teams=(me, opponent), names=names,
        )  # fmt: skip
    return _lineup_matchup(
        session, league, rules, rows, value, opening=opening, week=week, week_start=week_start,
        game_day=game_day, final=final, teams=(me, opponent), names=names,
    )  # fmt: skip


def _lock_in_matchup(
    session: Session,
    league: League,
    rows: Sequence[PlayerGameStatRow],
    value: GameValue,
    *,
    week: int,
    game_day: date,
    final: bool,
    teams: tuple[str, str],
    names: dict[str, str],
) -> MatchupDay:
    """Last night's game scores for each team's starters, before locks: not a total (only a locked
    game counts, and the app can't see locks). The starters are the lineup stored for that day,
    never today's roster; with none stored the figure is unknown, not zero."""
    scores = {
        r.player_id: value.of(stat_line(r)) for r in rows if r.played and r.game_date == game_day
    }
    stored_lineup = league_data.roster_on(session, league.id, game_day)
    stored = {team for team, _, _ in stored_lineup}
    on_day: dict[str, list[UUID]] = defaultdict(list)
    for team, pid, slot in stored_lineup:
        if slot not in NON_STARTING_SLOTS:  # bench and injured reserve don't count
            on_day[team].append(pid)

    def side(team: str) -> Side:
        return Side(
            team=team, name=names.get(team, team).strip(),
            day_points=(
                round(sum(scores.get(pid, 0.0) for pid in on_day[team]), 2)
                if team in stored
                else None
            ),
            reported_week=league_data.reported_matchup_points(session, league.id, week, team),
        )  # fmt: skip

    return MatchupDay(
        MatchupMode.LOCK_IN, week, final, side(teams[0]), side(teams[1]), Agreement(0, 0)
    )


def _lineup_matchup(
    session: Session,
    league: League,
    rules: LeagueRules,
    rows: Sequence[PlayerGameStatRow],
    value: GameValue,
    *,
    opening: date,
    week: int,
    week_start: date,
    game_day: date,
    final: bool,
    teams: tuple[str, str],
    names: dict[str, str],
) -> MatchupDay:
    points = value.is_points
    categories = rules.scoring.categories
    days = sorted(
        {day for _, _, day, _, _ in nba.games_between(session, league.season, week_start, game_day)}
        | {game_day}
    )
    in_week = [r for r in rows if r.played and week_start <= r.game_date <= game_day]
    lines: dict[date, dict[UUID, StatLine]] = defaultdict(dict)
    for r in in_week:
        lines[r.game_date][r.player_id] = stat_line(r)
    values = {day: {pid: value.of(line) for pid, line in by.items()} for day, by in lines.items()}

    # Each team's lineup for each day, and what it scored, worked out once and read by `side`.
    # None: no lineup was stored for that day (its total is unknown, not zero).
    day_total: dict[tuple[str, date], float | None] = {}
    day_lines: dict[tuple[str, date], list[StatLine] | None] = {}
    for day in days:
        on_day = league_data.roster_on(session, league.id, day)
        for team in teams:
            found = [(pid, slot) for t, pid, slot in on_day if t == team]
            if not found:
                day_total[team, day] = day_lines[team, day] = None
                continue
            day_total[team, day] = lineup_total(found, values.get(day, {}))
            day_lines[team, day] = [
                lines[day][pid]
                for pid, slot in found
                if slot not in NON_STARTING_SLOTS and pid in lines[day]
            ]

    ours: dict[str, float] = {}
    reported: dict[str, float] = {}
    tolerances: dict[str, float] = {}

    def side(team: str, who: str) -> Side:
        name = names.get(team, team).strip()
        if points:
            today_total = day_total[team, game_day]
            totals = [day_total[team, d] for d in days]
            week_total = (
                None if None in totals else round(sum(t for t in totals if t is not None), 2)
            )
            by_period = (
                league_data.reported_team_points(session, league.id, week, team)
                if league.platform is Platform.ESPN
                else {}
            )
            rep_day = by_period.get(scoring_period_of(game_day, opening))
            rep_week = (
                sum(by_period.get(scoring_period_of(d, opening), 0.0) for d in days)
                if by_period
                else None
            )
            for key, ours_value, theirs in (
                (f"{who}:day", today_total, rep_day),
                (f"{who}:week", week_total, rep_week),
            ):
                if ours_value is not None and theirs is not None:
                    ours[key], reported[key] = ours_value, theirs
            return Side(
                team=team, name=name, day_points=today_total, week_points=week_total,
                reported_day=rep_day, reported_week=rep_week,
            )  # fmt: skip
        today_lines = day_lines[team, game_day]
        per_day = [day_lines[team, d] for d in days]
        week_cats = (
            None
            if None in per_day
            else category_totals([ln for lns in per_day for ln in lns or []], categories)
        )
        rep_cats = (
            league_data.reported_category_scores(session, league.id, week, team)
            if league.platform is Platform.ESPN
            else {}
        )
        if week_cats is not None:
            for cat in categories:
                key = f"{who}:{cat.name}"
                tolerances[key] = RATIO_TOLERANCE if cat.ratio_of else CATEGORY_TOLERANCE
                if cat.name in rep_cats:
                    ours[key], reported[key] = week_cats[cat.name], rep_cats[cat.name]
        return Side(
            team=team, name=name,
            day_categories={} if today_lines is None else category_totals(today_lines, categories),
            week_categories=week_cats, reported_categories=rep_cats,
        )  # fmt: skip

    me, them = side(teams[0], "me"), side(teams[1], "them")
    mode = MatchupMode.POINTS if points else MatchupMode.CATEGORIES
    tolerance = tolerances or DAY_TOLERANCE
    checked = compare_totals(ours, reported, tolerance=tolerance)
    differences = tuple(
        TotalsDifference(
            side=key.partition(":")[0], what=key.partition(":")[2],
            ours=ours[key], theirs=reported[key],
        )
        for key in differing(ours, reported, tolerance=tolerance)
    )  # fmt: skip
    return MatchupDay(mode, week, final, me, them, checked, differences)
