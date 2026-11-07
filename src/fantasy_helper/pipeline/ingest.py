"""`fh ingest`: load every league and the public NBA data into the database (M1, M2).

Public data (projections, injury statuses, box scores, the schedule, Sleeper's player list) comes
from the
daily snapshots already in the raw store: every snapshot not yet loaded, oldest first, so the
history since snapshots began lands in order. League data is fetched fresh, and each response
is stored raw before it's mapped. Running ingest twice changes nothing the second time.
"""

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.adapters._http import RawResponse
from fantasy_helper.adapters.espn import league as espn_league
from fantasy_helper.adapters.espn import mapper as espn
from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.adapters.sleeper import league as sleeper_league
from fantasy_helper.adapters.sleeper import mapper as sleeper
from fantasy_helper.domain.calendar import (
    NBA_TIMEZONE,
    matchup_week_bounds,
    matchup_week_of,
    season_end_year,
)
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.ports import Clock, SystemClock
from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.records import (
    InjuryRecord,
    PlayerKey,
    ProjectionRecord,
    SourcePlayer,
)
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.domain.users import EspnCredentials, User
from fantasy_helper.identity.crosswalk import CrosswalkReport, Resolver, sync_players
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.pipeline.retry import classify_exception, outcome
from fantasy_helper.storage.raw_store import RawStore
from fantasy_helper.storage.repositories import credentials, database, leagues, nba, raw_loads
from fantasy_helper.storage.repositories import league_data as store_league

log = get_logger(__name__)

PUBLIC = {
    "espn_players": ("espn", "players_projections"),
    "espn_schedule": ("espn", "pro_schedule"),
    "sleeper_state": ("sleeper", "state"),
    "sleeper_players": ("sleeper", "players"),
}


GAME_LOGS = ("espn", "game_logs")
# Supabase Free counts 500 MB of database size. Warn well before it (ADR 0021).
MB = 1024 * 1024
DATABASE_QUOTA_BYTES = 500 * MB
DATABASE_WARN_AT = (0.6, 0.8)


class MissingSnapshotError(Exception):
    pass


PLATFORM_NAMES = {Platform.ESPN: "ESPN", Platform.SLEEPER: "Sleeper", Platform.YAHOO: "Yahoo"}


@dataclass
class LeagueResult:
    league: League
    failure: FailureKind | None = None  # set when this league couldn't be fetched or stored
    settings_version: int = 0
    new_settings: bool = False
    unmapped_scoring: tuple[str, ...] = ()
    teams: int = 0
    roster_entries: int = 0
    matchups: int = 0
    transactions: int = 0
    unresolved_players: int = 0
    trades_without_players: int = 0  # ESPN hides other teams' proposals (M1 spike)
    rostered_sleeper: int = 0
    rostered_sleeper_linked: int = 0  # linked to an ESPN player
    free_agents: int = 0  # Sleeper only: every active player not on a roster in the league
    free_agents_linked: int = 0
    # ESPN points leagues: each rostered player's season points as ESPN reports them, by ESPN
    # player ID. For the daily check of our box scores and scoring only (ADR 0022).
    reported_season_points: dict[str, float] = field(default_factory=dict)


@dataclass
class IngestReport:
    crosswalk: CrosswalkReport = field(default_factory=CrosswalkReport)
    games: int = 0
    projection_snapshots: int = 0
    projection_rows: int = 0
    injury_changes: int = 0
    game_log_payloads: int = 0
    game_log_rows: int = 0
    game_logs_unresolved: int = 0  # player not in the crosswalk
    game_logs_unknown_game: int = 0  # game not in the schedule
    database_bytes: int = 0
    leagues: list[LeagueResult] = field(default_factory=list)  # every league, loaded or failed
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Fetched:
    league: League
    data: dict[str, Any]


def run_ingest(
    session: Session,
    client: httpx.Client,
    *,
    store: RawStore,
    user: User,
    key: bytes,
    clock: Clock | None = None,
    events: RunEvents | None = None,
) -> IngestReport:
    """Load everything. `events` gets one quiet step event per stage (counts only)."""
    clock = clock or SystemClock()
    now = clock.now()
    # The NBA's calendar (Eastern), like every loader that reads these snapshots: a Pacific
    # user's late evening is already tomorrow's NBA day.
    today = now.astimezone(NBA_TIMEZONE).date()
    report = IngestReport()

    latest = {name: _latest(store, *src) for name, src in PUBLIC.items()}
    espn_players_raw = json.loads(store.get(latest["espn_players"]))
    sleeper_players_raw = json.loads(store.get(latest["sleeper_players"]))
    sleeper_week = int(json.loads(store.get(latest["sleeper_state"]))["week"])

    # The schedule first: fetching league data needs it (this matchup week's finished days).
    schedule_raw = json.loads(store.get(latest["espn_schedule"]))
    report.games = nba.upsert_games(
        session, season_end_year(today), espn.map_schedule(schedule_raw)
    )

    fetched: list[_Fetched] = []
    user_leagues = leagues.list_for_user(session, user.id)
    for league in user_leagues:
        try:
            if league.platform is Platform.ESPN:
                fetched.append(_fetch_espn(session, client, store, league, user, key, today))
            elif league.platform is Platform.SLEEPER:
                fetched.append(_fetch_sleeper(client, store, league, sleeper_week))
        except Exception as exc:  # one league failing never stops the others
            kind = classify_exception(exc)
            report.leagues.append(LeagueResult(league=league, failure=kind))
            report.errors.append(
                f"{league.platform.value} league {league.name!r}: {exc} ({kind.value})"
            )

    _step(events, "League data fetched", f"{len(fetched)} of {len(user_leagues)} leagues.")

    athletes = store.keys("espn", "athletes")  # birth dates; optional (added 2026-11-06)
    births = espn.map_birth_dates(json.loads(store.get(athletes[-1]))) if athletes else {}
    espn_players = espn.map_players(espn_players_raw, births) + [
        p for f in fetched if f.league.platform is Platform.ESPN
        for p in espn.map_roster_players(f.data["teams_rosters"], births)
    ]  # fmt: skip
    rostered_sleeper = {
        str(pid) for f in fetched if f.league.platform is Platform.SLEEPER
        for roster in f.data["rosters"] for pid in roster.get("players") or []
    }  # fmt: skip
    sleeper_players = sleeper.map_players(sleeper_players_raw, include=rostered_sleeper)
    report.crosswalk = sync_players(session, espn_players, sleeper_players)
    resolver = Resolver(session)

    _load_projections(session, store, resolver, report)
    _load_injuries(session, store, resolver, report)
    _load_game_logs(session, store, resolver, report)
    if report.game_logs_unknown_game:
        report.warnings.append(
            f"{report.game_logs_unknown_game} box score rows name a game missing from the schedule"
        )
    _step(
        events,
        "Public data loaded",
        f"{report.games} games, {report.projection_rows} projection rows from "
        f"{report.projection_snapshots} snapshots, {report.injury_changes} injury changes, "
        f"{report.game_log_rows} box score rows.",
    )

    for f in fetched:
        try:
            with session.begin_nested():  # a league that can't be stored leaves no partial rows
                result = (
                    _store_espn(session, f, resolver, now, today)
                    if f.league.platform is Platform.ESPN
                    else _store_sleeper(session, f, resolver, now, today, sleeper_players)
                )
        except Exception as exc:
            kind = classify_exception(exc)
            log.error("league_store_failed", platform=f.league.platform.value, kind=kind.value)
            report.leagues.append(LeagueResult(league=f.league, failure=kind))
            report.errors.append(
                f"{f.league.name!r}: could not store ({type(exc).__name__}: {exc}; {kind.value})"
            )
            continue
        report.leagues.append(result)
        if result.unresolved_players:
            report.errors.append(
                f"{f.league.name!r}: {result.unresolved_players} rostered players unresolved"
            )
        if result.unmapped_scoring:
            report.errors.append(
                f"{f.league.name!r}: unmapped scoring {', '.join(result.unmapped_scoring)}"
            )
        unlinked = result.rostered_sleeper - result.rostered_sleeper_linked
        if unlinked:
            report.warnings.append(
                f"{f.league.name!r}: {unlinked} rostered Sleeper players not linked to ESPN "
                "(add them to identity/overrides.py)"
            )
    loaded = [lg for lg in report.leagues if lg.failure is None]
    _step(
        events,
        "Leagues stored",
        f"{len(loaded)} of {len(user_leagues)} leagues: "
        f"{sum(lg.roster_entries for lg in report.leagues)} roster entries, "
        f"{sum(lg.matchups for lg in report.leagues)} matchups, "
        f"{sum(lg.transactions for lg in report.leagues)} transactions.",
    )
    session.flush()
    report.database_bytes = database.size_bytes(session)
    share = report.database_bytes / DATABASE_QUOTA_BYTES
    if share >= DATABASE_WARN_AT[0]:
        report.warnings.append(
            f"database is {report.database_bytes / MB:.0f} MB, {share:.0%} of the free plan's "
            "500 MB (ADR 0021: time for the next storage lever)"
        )
    log.info("ingest_finished", leagues=len(report.leagues), errors=len(report.errors))
    return report


def _step(events: RunEvents | None, name: str, detail: str) -> None:
    if events is not None:
        events.step(name, detail)


# ------------------------------------------------------------------------------- fetching


def _latest(store: RawStore, source: str, dataset: str) -> str:
    keys = store.keys(source, dataset)
    if not keys:
        raise MissingSnapshotError(f"no {source} {dataset} snapshot yet; run `fh snapshot` first")
    return keys[-1]


def _keep(store: RawStore, league: League, response: RawResponse) -> Any:
    store.put(
        source=league.platform.value,
        dataset=f"leagues/{league.external_id}/{response.dataset}",
        fetched_at=response.fetched_at,
        body=response.body,
        meta={
            "url": response.url,
            "params": dict(response.params),
            "status_code": response.status_code,
        },
    )
    return json.loads(response.body)


def _fetch_espn(
    session: Session,
    client: httpx.Client,
    store: RawStore,
    league: League,
    user: User,
    key: bytes,
    today: date,
) -> _Fetched:
    creds = credentials.load_espn(session, user.id, key)
    if creds is None:
        raise EspnAccessError("no ESPN cookies saved", 401)  # needs `fh league add`: AUTH
    args = (client, league.season, league.external_id, creds)
    settings = _keep(store, league, espn_league.fetch_settings(*args))
    latest_period = espn.league_status(settings).latest_scoring_period
    return _Fetched(
        league,
        {
            "settings": settings,
            "teams_rosters": _keep(store, league, espn_league.fetch_teams_rosters(*args)),
            "matchups": _keep(store, league, espn_league.fetch_matchups(*args)),
            "transactions": [
                _keep(store, league, espn_league.fetch_transactions(*args, period))
                for period in range(1, latest_period + 1)
            ],
            # Each finished day of this matchup week: who was in which slot (for points banked
            # so far, scored from our box scores; ADR 0022).
            "lineups": _fetch_lineups(
                store, league, args, _finished_days(session, league.season, today)
            ),
        },
    )


def _fetch_lineups(
    store: RawStore,
    league: League,
    args: tuple[httpx.Client, int, str, EspnCredentials],
    days: list[tuple[date, int]],
) -> dict[date, Any]:
    """Each finished day's lineup on its own: a day that fails is skipped (its banked points
    then read as unknown), never the whole league."""
    out: dict[date, Any] = {}
    for day, period in days:
        try:
            out[day] = _keep(store, league, espn_league.fetch_lineups(*args, period))
        except (EspnAccessError, httpx.HTTPError) as exc:
            log.warning("lineup_fetch_failed", period=period, error=type(exc).__name__)
    return out


def _finished_days(session: Session, season: int, today: date) -> list[tuple[date, int]]:
    """(day, ESPN scoring period) for each day of the current matchup week before today."""
    opening = nba.season_start(session, season)
    if opening is None:  # no schedule yet (the very first ingest)
        return []
    start, _ = matchup_week_bounds(opening, matchup_week_of(opening, today))
    days = [start + timedelta(days=i) for i in range((today - start).days)]
    return [(day, espn.scoring_period_of(day, opening)) for day in days]


def _fetch_sleeper(client: httpx.Client, store: RawStore, league: League, week: int) -> _Fetched:
    lid = league.external_id
    return _Fetched(
        league,
        {
            "league": _keep(store, league, sleeper_league.fetch_league(client, lid)),
            "users": _keep(store, league, sleeper_league.fetch_users(client, lid)),
            "rosters": _keep(store, league, sleeper_league.fetch_rosters(client, lid)),
            "matchups": {
                wk: _keep(store, league, sleeper_league.fetch_matchups(client, lid, wk))
                for wk in range(1, week + 1)
            },
            "transactions": {
                wk: _keep(store, league, sleeper_league.fetch_transactions(client, lid, wk))
                for wk in range(1, week + 1)
            },
        },
    )


# -------------------------------------------------------------------------- public data


def _resolved(resolver: Resolver, records: list[Any]) -> list[tuple[Any, Any]]:
    out = []
    for record in records:
        player_id = resolver.resolve(record.player)
        if player_id is not None:
            out.append((record, player_id))
    return out


# Projection datasets in the raw store: (source, dataset) -> provider, scope, mapper.
_PROJECTION_DATASETS: dict[
    tuple[str, str], tuple[Provider, ProjectionScope, Callable[[Any, int], list[ProjectionRecord]]]
] = {
    ("espn", "players_projections"): (
        Provider.ESPN, ProjectionScope.SEASON,
        lambda data, season: espn.map_projections(data, season),
    ),
    ("sleeper", "projections_season"): (
        Provider.SLEEPER, ProjectionScope.SEASON,
        lambda data, _: sleeper.map_projections(data, per_game_rows=False),
    ),
    ("sleeper", "projections_week"): (
        Provider.SLEEPER, ProjectionScope.WEEK,
        lambda data, _: sleeper.map_projections(data, per_game_rows=True),
    ),
}  # fmt: skip


def _load_projections(
    session: Session, store: RawStore, resolver: Resolver, report: IngestReport
) -> None:
    for (source, dataset), (provider, scope, to_records) in _PROJECTION_DATASETS.items():
        for key in store.keys(source, dataset):
            if nba.snapshot_loaded(session, key):
                continue
            meta = store.meta(key)
            pulled_at = datetime.fromisoformat(meta["fetched_at"])
            season = season_end_year(pulled_at.date())
            week = (
                re.search(r"/(\d+)$", meta.get("url", ""))
                if scope is ProjectionScope.WEEK
                else None
            )
            report.projection_rows += nba.load_projections(
                session,
                provider=provider,
                scope=scope,
                season=season,
                period=week.group(1) if week else None,
                pulled_at=pulled_at,
                raw_key=key,
                rows=_resolved(resolver, to_records(json.loads(store.get(key)), season)),
            )
            report.projection_snapshots += 1


def _load_game_logs(
    session: Session, store: RawStore, resolver: Resolver, report: IngestReport
) -> None:
    """Box scores from every game-log payload not loaded yet. Each payload covers each player's
    last few games, so payloads overlap; the upsert keeps one row per player and game."""
    dataset = "/".join(GAME_LOGS)
    done = raw_loads.loaded_keys(session, dataset)
    games: dict[int, dict[str, tuple[Any, date]]] = {}
    for key in store.keys(*GAME_LOGS):
        if key in done:
            continue
        season = season_end_year(datetime.fromisoformat(store.meta(key)["fetched_at"]).date())
        index = games.setdefault(season, nba.game_index(session, season))
        rows = []
        for record in espn.map_game_logs(json.loads(store.get(key)), season):
            player_id = resolver.resolve(record.player)
            game = index.get(record.external_game_id)
            if player_id is None:
                report.game_logs_unresolved += 1
            elif game is None:
                report.game_logs_unknown_game += 1
            else:
                rows.append((record, player_id, *game))
        written = nba.upsert_game_logs(session, rows)
        raw_loads.mark_loaded(session, key, dataset=dataset, rows=written)
        report.game_log_payloads += 1
        report.game_log_rows += written


def _load_injuries(
    session: Session, store: RawStore, resolver: Resolver, report: IngestReport
) -> None:
    for provider, (source, dataset) in (
        (Provider.ESPN, ("espn", "players_projections")),
        (Provider.SLEEPER, ("sleeper", "players")),
    ):
        since = nba.latest_injury_observed_at(session, provider)
        for key in store.keys(source, dataset):
            observed_at = datetime.fromisoformat(store.meta(key)["fetched_at"])
            if since is not None and observed_at <= since:
                continue
            data = json.loads(store.get(key))
            records: list[InjuryRecord] = (
                espn.map_injuries(data) if provider is Provider.ESPN else sleeper.map_injuries(data)
            )
            report.injury_changes += nba.record_injury_changes(
                session, provider=provider, observed_at=observed_at, raw_key=key,
                observations=_resolved(resolver, records),
            )  # fmt: skip


# ------------------------------------------------------------------------- league data


def _store_roster(
    session: Session, result: LeagueResult, resolver: Resolver, entries: list[Any], today: date
) -> None:
    resolved = []
    for entry in entries:
        player_id = resolver.resolve(entry.player)
        if player_id is None:
            result.unresolved_players += 1
        else:
            resolved.append((entry, player_id))
    result.roster_entries = store_league.replace_roster(session, result.league.id, today, resolved)


def _store_espn(
    session: Session, f: _Fetched, resolver: Resolver, now: datetime, today: date
) -> LeagueResult:
    result = LeagueResult(league=f.league)
    rules = espn.map_rules(f.data["settings"])
    result.settings_version, result.new_settings = store_league.save_rules(
        session, f.league.id, rules, now
    )
    result.unmapped_scoring = rules.unmapped_scoring
    teams, entries = espn.map_rosters(f.data["teams_rosters"])
    result.teams = store_league.upsert_teams(session, f.league.id, teams)
    if rules.rules.scoring.weights:
        result.reported_season_points = espn.map_season_points(
            f.data["teams_rosters"], f.league.season
        )
    _store_roster(session, result, resolver, entries, today)
    for day, lineup in f.data.get("lineups", {}).items():
        resolved = [
            (e, pid) for e in espn.map_lineup(lineup) if (pid := resolver.resolve(e.player))
        ]
        store_league.replace_roster(session, f.league.id, day, resolved)
    result.matchups = store_league.upsert_matchups(
        session, f.league.id, espn.map_matchups(f.data["matchups"])
    )
    records, result.trades_without_players = espn.map_transactions(f.data["transactions"])
    result.transactions = store_league.upsert_transactions(session, f.league.id, records)
    return result


def _store_sleeper(
    session: Session,
    f: _Fetched,
    resolver: Resolver,
    now: datetime,
    today: date,
    all_players: list[SourcePlayer],
) -> LeagueResult:
    result = LeagueResult(league=f.league)
    rules = sleeper.map_rules(f.data["league"])
    result.settings_version, result.new_settings = store_league.save_rules(
        session, f.league.id, rules, now
    )
    result.unmapped_scoring = rules.unmapped_scoring
    teams, entries = sleeper.map_rosters(f.data["league"], f.data["rosters"], f.data["users"])
    result.teams = store_league.upsert_teams(session, f.league.id, teams)
    _store_roster(session, result, resolver, entries, today)
    result.rostered_sleeper = len({e.player.external_id for e in entries})
    result.rostered_sleeper_linked = sum(
        1 for pid in {e.player.external_id for e in entries}
        if resolver.linked_to_espn(PlayerKey(source=IdSource.SLEEPER, external_id=pid))
    )  # fmt: skip
    rostered = {e.player.external_id for e in entries}
    free_agents = [p for p in all_players if p.active and p.key.external_id not in rostered]
    result.free_agents = len(free_agents)
    result.free_agents_linked = sum(1 for p in free_agents if resolver.linked_to_espn(p.key))
    matchups = [
        m for wk, data in f.data["matchups"].items() for m in sleeper.map_matchups(int(wk), data)
    ]
    result.matchups = store_league.upsert_matchups(session, f.league.id, matchups)
    records = [
        r for data in f.data["transactions"].values() for r in sleeper.map_transactions(data)
    ]
    result.transactions = store_league.upsert_transactions(session, f.league.id, records)
    return result


def phone_summary(report: IngestReport, attempts: int) -> tuple[str, list[str]]:
    """What the phone hears about an ingest: counts and kinds only, never names (ADR 0011).

    Built from the report's numbers, not its error text, so it reads the same with 3 leagues
    or 15: how many loaded, how many failed and why, and what needs a look.
    """
    total = len(report.leagues)
    loaded = [lg for lg in report.leagues if lg.failure is None]
    share = report.database_bytes / DATABASE_QUOTA_BYTES
    detail = (
        f"Leagues: {len(loaded)} of {total} loaded. Box scores: {report.game_log_rows} new rows; "
        f"projections: {report.projection_rows} new rows. "
        f"Database {report.database_bytes / MB:.0f} MB ({share:.0%} of the free plan)."
    )
    problems: list[str] = []
    failed = Counter(
        (PLATFORM_NAMES[lg.league.platform], lg.failure)
        for lg in report.leagues
        if lg.failure is not None
    )
    for (platform, kind), n in sorted(failed.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
        problems.append(
            f"{n} {platform} league{'s' if n > 1 else ''} failed: {outcome(kind, attempts)}"
        )
    unresolved = [lg for lg in loaded if lg.unresolved_players]
    if unresolved:
        players = sum(lg.unresolved_players for lg in unresolved)
        problems.append(f"{players} rostered players unresolved in {len(unresolved)} leagues")
    unlinked = sum(lg.rostered_sleeper - lg.rostered_sleeper_linked for lg in loaded)
    if unlinked:
        problems.append(f"{unlinked} rostered Sleeper players not linked to ESPN (add overrides)")
    unmapped = sum(1 for lg in loaded if lg.unmapped_scoring)
    if unmapped:
        problems.append(f"{unmapped} leagues score a setting with no box score stat yet")
    if report.game_logs_unknown_game:
        problems.append(
            f"{report.game_logs_unknown_game} box score rows name a game missing from the schedule"
        )
    if share >= DATABASE_WARN_AT[0]:
        problems.append(f"database at {share:.0%} of the free plan (ADR 0021: next storage lever)")
    return detail, problems
