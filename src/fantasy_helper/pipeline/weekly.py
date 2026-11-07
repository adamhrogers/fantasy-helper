"""`fh run weekly`: the weekly overview, built entirely by code (M2).

Per league, for the rest of the matchup week:
- both teams' started games projected (daily lineups; lock-in leagues count each starter's game
  under optimal locking), and the win probability
- games and startable games per day
- replacement level and value over replacement
- streaming moves within the league's add limit (daily-lineup points leagues), only dropping
  players who are replaceable on their season average
- the FactSheet (append-only) and the league's player values (replaced), ADR 0020

The moves and the matchup forecast are calls: logged in the registry before tip-off (ADR 0005).
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.facts import (
    FactDay,
    FactMatchup,
    FactMove,
    FactPlayer,
    FactSheet,
    SheetKind,
)
from fantasy_helper.domain.league import League, LineupMode
from fantasy_helper.domain.ports import Clock, SystemClock
from fantasy_helper.domain.recommendations import (
    Action,
    CallInputs,
    CallType,
    Claim,
    Move,
    OutcomeWindow,
    Recommendation,
)
from fantasy_helper.domain.reports import ReportDocument
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.domain.users import User
from fantasy_helper.engine.baselines import top_projected_free_agent
from fantasy_helper.engine.distributions import normal_cdf
from fantasy_helper.engine.lock_in import week_value
from fantasy_helper.engine.schedule import PlayerWeek, WeekProjection, fill_day, project_week
from fantasy_helper.engine.schedule import starting_slots as expand_slots
from fantasy_helper.engine.streaming import StreamMove, plan_streams
from fantasy_helper.engine.valuation import replacement_levels, value_over_replacement
from fantasy_helper.engine.win_probability import win_probability
from fantasy_helper.facts.weekly import (
    TeamWeek,
    WeeklySheet,
    WeekPlayer,
    distribution_for_count,
    load_weekly,
)
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.pipeline.ingest import PLATFORM_NAMES, IngestReport, phone_summary, run_ingest
from fantasy_helper.pipeline.lock_calls import ENGINE_PRODUCER
from fantasy_helper.pipeline.retry import classify_exception, outcome
from fantasy_helper.registry.calls import log_call
from fantasy_helper.reports.weekly import weekly_report
from fantasy_helper.storage.raw_store import RawStore
from fantasy_helper.storage.repositories import leagues, nba, read_models
from fantasy_helper.storage.tables import PlayerGameStatRow

log = get_logger(__name__)
FREE_AGENTS_IN_SHEET = 12
# Our banked points should match ESPN's daily totals exactly (same box scores, same lineups);
# a gap past this means a lineup or box score is off.
BANKED_TOLERANCE = 1.0


@dataclass
class LeagueWeekly:
    league: League
    sheet: FactSheet | None = None
    report: ReportDocument | None = None
    calls: int = 0
    new_calls: int = 0
    failure: FailureKind | None = None
    banked_gap: float | None = None  # our banked points minus the platform's (a check only)
    period_mismatch: bool = False  # ESPN's matchup spans several calendar weeks: no forecast


@dataclass
class WeeklyResult:
    ingest: IngestReport
    leagues: list[LeagueWeekly] = field(default_factory=list)

    @property
    def reports(self) -> list[ReportDocument]:
        return [lg.report for lg in self.leagues if lg.report is not None]

    def retryable_failures(self) -> int:
        parts = [lg.failure for lg in self.ingest.leagues] + [lg.failure for lg in self.leagues]
        return sum(1 for f in parts if f is not None and f.retryable)


def run_weekly(
    session: Session,
    client: httpx.Client,
    *,
    store: RawStore,
    user: User,
    key: bytes,
    clock: Clock | None = None,
    events: RunEvents | None = None,
) -> WeeklyResult:
    clock = clock or SystemClock()
    ingest = run_ingest(
        session, client, store=store, user=user, key=key, clock=clock, events=events
    )
    result = WeeklyResult(ingest=ingest)
    now = clock.now()
    failed = {lg.league.id: lg.failure for lg in ingest.leagues if lg.failure}
    box_scores: dict[int, list[PlayerGameStatRow]] = {}  # one load per season, shared by leagues
    for league in leagues.list_for_user(session, user.id):
        if league.id in failed:
            result.leagues.append(LeagueWeekly(league, failure=failed[league.id]))
            continue
        if league.season not in box_scores:
            box_scores[league.season] = nba.box_scores(session, league.season)
        try:
            with session.begin_nested():
                result.leagues.append(
                    _league_week(session, league, user, now, box_scores[league.season])
                )
        except Exception as exc:
            kind = classify_exception(exc)
            log.error("weekly_league_failed", platform=league.platform.value, kind=kind.value)
            result.leagues.append(LeagueWeekly(league, failure=kind))
    if events is not None:
        done = [lg for lg in result.leagues if lg.failure is None]
        moves = sum(len(lg.sheet.moves) for lg in done if lg.sheet)
        events.step(
            "Weekly overview built",
            f"{len(done)} of {len(result.leagues)} leagues; {moves} streaming moves; "
            f"{sum(lg.new_calls for lg in done)} calls newly logged.",
        )
    return result


def protected_from_drops(players: Sequence[WeekPlayer], vor: dict[UUID, float]) -> frozenset[UUID]:
    """Players never suggested as drops: anyone above replacement on his season average, and
    anyone without one yet (fewer than 3 games: back from injury, a rookie), whose value can't
    be judged from a week with no games."""
    return frozenset(
        p.player_id for p in players if p.season_per_game is None or vor.get(p.player_id, 0) > 0
    )


def _weeks(players: Sequence[WeekPlayer], points: bool) -> list[PlayerWeek]:
    return [p.week if points else distribution_for_count(p.week) for p in players]


def _project(sheet: WeeklySheet, team: TeamWeek, points: bool) -> WeekProjection:
    slots = sheet.rules.roster_slots
    if sheet.rules.lineup_mode is not LineupMode.LOCK_IN:
        return project_week(_weeks(team.active, points), slots)
    # Lock-in: each starter counts one game, the one optimal locking would keep.
    values = {
        p.player_id: week_value(
            [d for _, d in p.week.games], latest=p.lockable_points, seed=p.player_id.int % 2**31
        )
        for p in team.active
    }
    chosen = fill_day(
        [(p.player_id, p.positions, values[p.player_id][0]) for p in team.active
         if p.week.games or p.lockable_points is not None],
        expand_slots(slots),
    )  # fmt: skip
    by_id = {p.player_id: p for p in team.active}
    return WeekProjection(
        started={
            pid: by_id[pid].week.games[0][:1] if by_id[pid].week.games else () for pid in chosen
        },
        points=sum(values[pid][0] for pid in chosen),
        variance=sum(values[pid][1] for pid in chosen),
        games=sum(len(p.week.games) for p in team.active),
        startable=len(chosen),
    )


def _days(sheet: WeeklySheet, projection: WeekProjection, points: bool) -> tuple[FactDay, ...]:
    slots = len(expand_slots(sheet.rules.roster_slots))
    counts: dict[date, list[int]] = {}
    for p in sheet.mine.active:
        for day, _ in p.week.games:
            counts.setdefault(day, [0, 0])[0] += 1
    for days in projection.started.values():
        for day in days:
            counts.setdefault(day, [0, 0])[1] += 1
    return tuple(
        FactDay(day=d, games=g, started=s, slots=slots) for d, (g, s) in sorted(counts.items())
    )


def _league_week(
    session: Session,
    league: League,
    user: User,
    now: datetime,
    box_scores: Sequence[PlayerGameStatRow],
) -> LeagueWeekly:
    ws = load_weekly(session, league, now=now, box_scores=box_scores)
    points = bool(ws.rules.scoring.weights)
    lock_in = ws.rules.lineup_mode is LineupMode.LOCK_IN
    slots = ws.rules.roster_slots
    mine = _project(ws, ws.mine, points)
    theirs = _project(ws, ws.opponent, points) if ws.opponent else None

    levels = replacement_levels([(fa.positions, fa.per_game) for fa in ws.free_agents], slots)

    def season(p: WeekPlayer) -> float:
        return p.season_per_game if p.season_per_game is not None else p.per_game

    # Value over replacement means points: categories leagues get none (P9).
    vor = (
        {
            p.player_id: value_over_replacement(season(p), p.positions, slots, levels)
            for p in ws.mine.players
        }
        if points
        else {}
    )
    moves: list[StreamMove] = []
    if points and not lock_in and ws.adds_left:
        keep = protected_from_drops(ws.mine.players, vor)
        moves = plan_streams(
            [p.week for p in ws.mine.active],
            [fa.week for fa in ws.free_agents],
            slots,
            adds_left=ws.adds_left,
            keep=keep,
        )

    matchup = None
    if points and theirs is not None and ws.opponent is not None:
        whole_week = ws.from_day == ws.week_start
        banked = (0.0, 0.0) if whole_week else (ws.my_banked, ws.their_banked)
        my_banked, their_banked = banked
        known = my_banked is not None and their_banked is not None
        # Final totals: points banked (our box scores x each day's lineup) plus the rest of
        # the week. The spread is the rest of the week's alone: banked points are settled.
        my_total = (my_banked or 0.0) + mine.points
        their_total = (their_banked or 0.0) + theirs.points
        matchup = FactMatchup(
            opponent=ws.opponent.name,
            my_points=round(my_total, 1),
            my_sd=round(math.sqrt(mine.variance), 1),
            their_points=round(their_total, 1),
            their_sd=round(math.sqrt(theirs.variance), 1),
            my_banked=None if whole_week or my_banked is None else round(my_banked, 1),
            their_banked=None if whole_week or their_banked is None else round(their_banked, 1),
            win_probability=round(
                win_probability(my_total, mine.variance, their_total, theirs.variance), 3
            )
            if known
            else None,
        )
    names = {p.player_id: p.name for p in (*ws.mine.players, *ws.free_agents)}
    fact_sheet = FactSheet(
        league_id=league.id, kind=SheetKind.WEEKLY, week=ws.week, from_day=ws.from_day,
        as_of=now, settings_version=ws.settings_version,
        projection_snapshot_ids=ws.projection_snapshot_ids, points_league=points,
        games=mine.games, startable=mine.startable, days=_days(ws, mine, points),
        matchup=matchup,
        my_players=tuple(
            FactPlayer(
                player_id=p.player_id, name=p.name, nba_team=p.nba_team, slot=p.slot,
                games=len(p.week.games), started=mine.started_games(p.player_id),
                per_game=round(p.per_game, 1),
                value_over_replacement=round(vor[p.player_id], 1) if p.player_id in vor else None,
            )
            for p in sorted(ws.mine.players, key=lambda p: -p.expected)
        ),
        free_agents=tuple(
            FactPlayer(
                player_id=fa.player_id, name=fa.name, nba_team=fa.nba_team, slot="FA",
                games=len(fa.week.games), started=0, per_game=round(fa.per_game, 1),
            )
            for fa in sorted(ws.free_agents, key=lambda f: -f.expected)[:FREE_AGENTS_IN_SHEET]
        ),
        moves=tuple(
            FactMove(add=m.add, add_name=names.get(m.add, "?"), drop=m.drop,
                     drop_name=names.get(m.drop, "?"), gain=round(m.gain, 1),
                     games_added=m.games_added)
            for m in moves
        ),
    )  # fmt: skip
    read_models.save_fact_sheet(session, fact_sheet)
    read_models.replace_player_values(session, league.id, _player_values(ws, mine, vor, now))

    logged: list[tuple[str, Recommendation]] = []
    new = 0
    if ws.first_tip_utc is not None and ws.first_tip_utc > now:
        for key, rec in _calls(ws, fact_sheet, user, now):
            stored, created = log_call(session, rec, key, now=now)
            logged.append((key, stored))
            new += int(created)
    report = weekly_report(fact_sheet, logged, user_id=user.id, generated_at=now, lock_in=lock_in)
    gap = None
    if fact_sheet.matchup and fact_sheet.matchup.my_banked is not None and ws.my_banked_reported:
        gap = round(fact_sheet.matchup.my_banked - ws.my_banked_reported, 1)
    return LeagueWeekly(
        league, sheet=fact_sheet, report=report, calls=len(logged), new_calls=new,
        banked_gap=gap, period_mismatch=ws.period_mismatch,
    )  # fmt: skip


def _player_values(
    ws: WeeklySheet, mine: WeekProjection, vor: dict[UUID, float], now: datetime
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for p in ws.mine.players:
        rows.append({
            "player_id": p.player_id, "as_of": now, "team_external_id": ws.mine.team_external_id,
            "slot": p.slot, "games_left": len(p.week.games),
            "startable_left": mine.started_games(p.player_id), "per_game": round(p.per_game, 2),
            "value_over_replacement": (
                round(vor[p.player_id], 2) if p.player_id in vor else None
            ),
        })  # fmt: skip
    for fa in ws.free_agents:
        rows.append({
            "player_id": fa.player_id, "as_of": now, "team_external_id": None, "slot": "FA",
            "games_left": len(fa.week.games), "startable_left": 0,
            "per_game": round(fa.per_game, 2), "value_over_replacement": None,
        })  # fmt: skip
    return rows


def _calls(
    ws: WeeklySheet, sheet: FactSheet, user: User, now: datetime
) -> list[tuple[str, Recommendation]]:
    assert ws.first_tip_utc is not None  # noqa: S101 (checked by the caller)
    week_over = datetime.combine(ws.week_end + timedelta(days=1), time(8), NBA_TIMEZONE)
    window = OutcomeWindow(start=ws.first_tip_utc, end=week_over)
    inputs = CallInputs(
        projection_snapshot_ids=ws.projection_snapshot_ids, settings_version=ws.settings_version
    )
    base = {
        "created_at": now, "league_id": ws.league.id, "user_id": user.id,
        "platform": ws.league.platform, "format": ws.rules.format, "inputs": inputs,
        "producer": ENGINE_PRODUCER, "outcome_window": window, "first_tip_utc": ws.first_tip_utc,
    }  # fmt: skip
    calls: list[tuple[str, Recommendation]] = []
    if sheet.matchup is not None and sheet.matchup.win_probability is not None:
        m = sheet.matchup
        p_win = m.win_probability
        claim = Claim(
            text=f"{p_win:.0%} to beat {m.opponent} "
            f"(projected {m.my_points:.0f} to {m.their_points:.0f})"
        )
        calls.append((
            f"matchup_odds:{ws.week}:{ws.from_day.isoformat()}",
            Recommendation.model_validate(base | {
                "call_type": CallType.MATCHUP_ODDS, "claim": claim,
                "expected_value": round(m.my_points - m.their_points, 1),
                "confidence": p_win,
                "baseline_call": Claim(text="A coin flip: 50%"),
                "detail": m.model_dump(mode="json") | {"week": ws.week},
            }),
        ))  # fmt: skip
    baseline_add = top_projected_free_agent([fa.week for fa in ws.free_agents])
    fa_by_id = {fa.player_id: fa for fa in ws.free_agents}
    for move in sheet.moves:
        added = fa_by_id.get(move.add)
        sd = math.sqrt(sum(d.sd**2 for _, d in added.week.games)) if added else 0.0
        confidence = normal_cdf(move.gain / sd) if sd > 0 else 1.0
        claim = Claim(
            text=f"Add {move.add_name}, drop {move.drop_name}: +{move.gain:.1f} expected points "
            f"this week ({move.games_added} more games started)",
            moves=(Move(action=Action.ADD, player_id=move.add),
                   Move(action=Action.DROP, player_id=move.drop)),
        )  # fmt: skip
        baseline = Claim(
            text="Add the free agent with the best projection per game",
            moves=(
                (Move(action=Action.ADD, player_id=baseline_add),
                 Move(action=Action.DROP, player_id=move.drop))
                if baseline_add else ()
            ),
        )  # fmt: skip
        calls.append((
            f"stream:{ws.week}:{move.add}:{move.drop}",
            Recommendation.model_validate(base | {
                "call_type": CallType.STREAM, "claim": claim, "expected_value": move.gain,
                "confidence": round(confidence, 3), "baseline_call": baseline,
                "detail": move.model_dump(mode="json") | {"week": ws.week},
            }),
        ))  # fmt: skip
    return calls


def weekly_summary(result: WeeklyResult, attempts: int) -> tuple[str, list[str]]:
    """What the phone hears: counts and kinds only, never names (ADR 0011)."""
    ingest_detail, problems = phone_summary(result.ingest, attempts)
    done = [lg for lg in result.leagues if lg.failure is None]
    moves = sum(len(lg.sheet.moves) for lg in done if lg.sheet)
    detail = (
        f"Weekly overview: {len(done)} of {len(result.leagues)} leagues, {moves} streaming "
        f"moves. {ingest_detail}"
    )
    ingest_failed = {lg.league.id for lg in result.ingest.leagues if lg.failure}
    failed = Counter(
        (PLATFORM_NAMES[lg.league.platform], lg.failure)
        for lg in result.leagues
        if lg.failure is not None and lg.league.id not in ingest_failed
    )
    for (platform, kind), n in sorted(failed.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
        problems.append(
            f"overview failed for {n} {platform} league{'s' if n > 1 else ''}: "
            f"{outcome(kind, attempts)}"
        )
    spanning = [lg for lg in done if lg.period_mismatch]
    if spanning:
        problems.append(
            f"{len(spanning)} league{'s' if len(spanning) > 1 else ''}' matchup spans more than "
            "this calendar week (the All-Star break?): matchup forecast skipped"
        )
    off = [lg for lg in done if lg.banked_gap is not None and abs(lg.banked_gap) > BANKED_TOLERANCE]
    if off:
        problems.append(
            f"points banked this week differ from the platform's in {len(off)} league"
            f"{'s' if len(off) > 1 else ''} (largest {_largest_gap(off):.1f}):"
            " check the stored lineups and box scores (ADR 0022)"
        )
    return detail, problems


def _largest_gap(leagues: list[LeagueWeekly]) -> float:
    return max(abs(lg.banked_gap or 0.0) for lg in leagues)
