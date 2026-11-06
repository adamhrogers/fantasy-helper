"""`fh ingest`: load every league and the public NBA data into the database (M1).

Public data (projections, injury statuses, the schedule, Sleeper's player list) comes from the
daily snapshots already in the raw store: every snapshot not yet loaded, oldest first, so the
history since snapshots began lands in order. League data is fetched fresh, and each response
is stored raw before it's mapped. Running ingest twice changes nothing the second time.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.adapters._http import RawResponse
from fantasy_helper.adapters.espn import league as espn_league
from fantasy_helper.adapters.espn import mapper as espn
from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.adapters.sleeper import league as sleeper_league
from fantasy_helper.adapters.sleeper import mapper as sleeper
from fantasy_helper.adapters.sleeper.league import SleeperLookupError
from fantasy_helper.domain.calendar import season_end_year
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.ports import Clock, SystemClock
from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.records import InjuryRecord, PlayerKey, ProjectionRecord
from fantasy_helper.domain.users import User
from fantasy_helper.identity.crosswalk import CrosswalkReport, Resolver, sync_players
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.storage.raw_store import RawStore
from fantasy_helper.storage.repositories import credentials, leagues, nba
from fantasy_helper.storage.repositories import league_data as store_league

log = get_logger(__name__)

PUBLIC = {
    "espn_players": ("espn", "players_projections"),
    "espn_schedule": ("espn", "pro_schedule"),
    "sleeper_state": ("sleeper", "state"),
    "sleeper_players": ("sleeper", "players"),
}


class MissingSnapshotError(Exception):
    pass


@dataclass
class LeagueResult:
    league: League
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


@dataclass
class IngestReport:
    crosswalk: CrosswalkReport = field(default_factory=CrosswalkReport)
    games: int = 0
    projection_snapshots: int = 0
    projection_rows: int = 0
    injury_changes: int = 0
    leagues: list[LeagueResult] = field(default_factory=list)
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
) -> IngestReport:
    clock = clock or SystemClock()
    now = clock.now()
    today = now.astimezone(ZoneInfo(user.timezone)).date()
    report = IngestReport()

    latest = {name: _latest(store, *src) for name, src in PUBLIC.items()}
    espn_players_raw = json.loads(store.get(latest["espn_players"]))
    sleeper_players_raw = json.loads(store.get(latest["sleeper_players"]))
    sleeper_week = int(json.loads(store.get(latest["sleeper_state"]))["week"])

    fetched: list[_Fetched] = []
    for league in leagues.list_for_user(session, user.id):
        try:
            if league.platform is Platform.ESPN:
                fetched.append(_fetch_espn(session, client, store, league, user, key))
            elif league.platform is Platform.SLEEPER:
                fetched.append(_fetch_sleeper(client, store, league, sleeper_week))
        except (EspnAccessError, SleeperLookupError, httpx.HTTPError) as exc:
            report.errors.append(f"{league.platform.value} league {league.name!r}: {exc}")

    espn_players = espn.map_players(espn_players_raw) + [
        p for f in fetched if f.league.platform is Platform.ESPN
        for p in espn.map_roster_players(f.data["teams_rosters"])
    ]  # fmt: skip
    rostered_sleeper = {
        str(pid) for f in fetched if f.league.platform is Platform.SLEEPER
        for roster in f.data["rosters"] for pid in roster.get("players") or []
    }  # fmt: skip
    report.crosswalk = sync_players(
        session, espn_players, sleeper.map_players(sleeper_players_raw, include=rostered_sleeper)
    )
    resolver = Resolver(session)

    schedule_raw = json.loads(store.get(latest["espn_schedule"]))
    report.games = nba.upsert_games(
        session, season_end_year(today), espn.map_schedule(schedule_raw)
    )
    _load_projections(session, store, resolver, report)
    _load_injuries(session, store, resolver, report)

    for f in fetched:
        result = (
            _store_espn(session, f, resolver, now, today)
            if f.league.platform is Platform.ESPN
            else _store_sleeper(session, f, resolver, now, today, sleeper_week)
        )
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
    log.info("ingest_finished", leagues=len(report.leagues), errors=len(report.errors))
    return report


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
    session: Session, client: httpx.Client, store: RawStore, league: League, user: User, key: bytes
) -> _Fetched:
    creds = credentials.load_espn(session, user.id, key)
    if creds is None:
        raise EspnAccessError("no ESPN cookies saved")
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
        },
    )


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
    _store_roster(session, result, resolver, entries, today)
    result.matchups = store_league.upsert_matchups(
        session, f.league.id, espn.map_matchups(f.data["matchups"])
    )
    records, result.trades_without_players = espn.map_transactions(f.data["transactions"])
    result.transactions = store_league.upsert_transactions(session, f.league.id, records)
    return result


def _store_sleeper(
    session: Session, f: _Fetched, resolver: Resolver, now: datetime, today: date, week: int
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
    matchups = [
        m for wk, data in f.data["matchups"].items() for m in sleeper.map_matchups(int(wk), data)
    ]
    result.matchups = store_league.upsert_matchups(session, f.league.id, matchups)
    records = [
        r for data in f.data["transactions"].values() for r in sleeper.map_transactions(data)
    ]
    result.transactions = store_league.upsert_transactions(session, f.league.id, records)
    return result
