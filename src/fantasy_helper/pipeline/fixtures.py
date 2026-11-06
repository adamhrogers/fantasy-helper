"""Record real API responses once, redacted, as test fixtures (CLAUDE.md rule 4, ADR 0012).

Everything is fetched and redacted first, then every file is checked against every original
value the redactor replaced, plus the user's cookies and real league IDs. Nothing is written
unless all files are clean.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.adapters._http import RawResponse
from fantasy_helper.adapters.espn import league as espn_league
from fantasy_helper.adapters.espn import public as espn_public
from fantasy_helper.adapters.redaction import (
    RedactionLeakError,
    Redactor,
    redact_espn,
    redact_sleeper,
)
from fantasy_helper.adapters.sleeper import league as sleeper_league
from fantasy_helper.adapters.sleeper import public as sleeper_public
from fantasy_helper.domain.league import Platform
from fantasy_helper.domain.users import User
from fantasy_helper.pipeline.onboarding import MissingCredentialsError
from fantasy_helper.storage.repositories import credentials, leagues

FIXTURES_ROOT = Path("tests/fixtures/recorded")
ESPN_PUBLIC_PLAYERS = 40
SCHEDULE_TEAMS_KEPT = 6


@dataclass(frozen=True)
class Fixture:
    path: str  # relative to the fixtures root, e.g. "espn_points/settings.json"
    data: Any
    note: str = ""

    def serialized(self) -> str:
        return json.dumps(self.data, separators=(",", ":"), ensure_ascii=False)


def _json(response: RawResponse) -> Any:
    return json.loads(response.body)


def _slim(data: Any) -> Any:
    """Fixture size only (each file stays under the 500 KB commit limit): empty players' stat
    arrays and drop the duplicate delayed copy of each matchup roster. Roster and matchup
    tests don't need player stats; projections and game logs have their own public fixtures."""
    if isinstance(data, dict):
        out = {k: _slim(v) for k, v in data.items() if k != "rosterForMatchupPeriodDelayed"}
        stats = out.get("stats")
        if (
            isinstance(stats, list)
            and stats
            and isinstance(stats[0], dict)
            and "statSplitTypeId" in stats[0]
        ):
            out["stats"] = []
        return out
    if isinstance(data, list):
        return [_slim(v) for v in data]
    return data


def _alias(base: str, taken: set[str]) -> str:
    alias, n = base, 2
    while alias in taken:
        alias, n = f"{base}_{n}", n + 1
    taken.add(alias)
    return alias


def record_fixtures(
    session: Session, client: httpx.Client, *, user: User, key: bytes
) -> list[Fixture]:
    r = Redactor()
    extra: set[str] = set()
    fixtures: list[Fixture] = []
    taken: set[str] = set()
    espn_player_ids: set[str] = set()
    sleeper_player_ids: set[str] = set()

    user_leagues = leagues.list_for_user(session, user.id)
    extra |= {lg.external_id for lg in user_leagues}
    creds = credentials.load_espn(session, user.id, key)
    if creds:
        swid = creds.swid.get_secret_value()
        extra |= {creds.espn_s2.get_secret_value(), swid, swid.strip("{}")}

    espn_season = None
    for lg in (x for x in user_leagues if x.platform is Platform.ESPN):
        if creds is None:
            raise MissingCredentialsError("no ESPN cookies saved; run `fh league add espn …` first")
        espn_season = lg.season
        args = (client, lg.season, lg.external_id, creds)
        settings = _json(espn_league.fetch_settings(*args))
        scoring = settings["settings"]["scoringSettings"]["scoringType"]
        alias = _alias("espn_points" if scoring == "H2H_POINTS" else "espn_categories", taken)
        rosters = _slim(_json(espn_league.fetch_teams_rosters(*args)))
        for team in rosters.get("teams", []):
            espn_player_ids |= {
                str(e["playerId"]) for e in team.get("roster", {}).get("entries", [])
            }
        matchups = _slim(_json(espn_league.fetch_matchups(*args)))
        latest = int(settings["status"]["latestScoringPeriod"])
        by_period = {
            str(p): redact_espn(
                _json(espn_league.fetch_transactions(*args, p)), r, league_id=lg.external_id
            )
            for p in range(1, latest + 1)
        }
        lid = lg.external_id
        fixtures += [
            Fixture(f"{alias}/settings.json", redact_espn(settings, r, league_id=lid),
                    "views mSettings + mStatus"),
            Fixture(f"{alias}/teams_rosters.json", redact_espn(rosters, r, league_id=lid),
                    "views mTeam + mRoster; players' stat arrays emptied for size"),
            Fixture(f"{alias}/matchups.json", redact_espn(matchups, r, league_id=lid),
                    "views mMatchupScore + mScoreboard; player stats emptied, "
                    "delayed roster copies dropped"),
            Fixture(
                f"{alias}/transactions.json",
                {"by_scoring_period": by_period},
                f"mTransactions2 per scoring period 1-{latest}, type-filtered",
            ),
        ]  # fmt: skip

    for lg in (x for x in user_leagues if x.platform is Platform.SLEEPER):
        week = int(_json(sleeper_public.state(client))["week"])
        alias = _alias("sleeper_points", taken)
        league_data = _json(sleeper_league.fetch_league(client, lg.external_id))
        users = _json(sleeper_league.fetch_users(client, lg.external_id))
        rosters = _json(sleeper_league.fetch_rosters(client, lg.external_id))
        for roster in rosters:
            sleeper_player_ids |= {str(p) for p in roster.get("players") or []}
        fixtures += [
            Fixture(f"{alias}/league.json", redact_sleeper("league", league_data, r)),
            Fixture(f"{alias}/users.json", redact_sleeper("users", users, r)),
            Fixture(f"{alias}/rosters.json", redact_sleeper("rosters", rosters, r)),
        ]
        for wk in range(1, week + 1):
            matchups = _json(sleeper_league.fetch_matchups(client, lg.external_id, wk))
            moves = _json(sleeper_league.fetch_transactions(client, lg.external_id, wk))
            fixtures += [
                Fixture(f"{alias}/matchups_{wk}.json", redact_sleeper("matchups", matchups, r)),
                Fixture(
                    f"{alias}/transactions_{wk}.json", redact_sleeper("transactions", moves, r)
                ),
            ]

    fixtures += _public_fixtures(client, espn_season, espn_player_ids, sleeper_player_ids)

    for fixture in fixtures:
        kinds = r.leaks(fixture.serialized(), extra)
        if kinds:
            raise RedactionLeakError(f"{fixture.path} would contain: {', '.join(kinds)}")
    return fixtures


def _public_fixtures(
    client: httpx.Client,
    espn_season: int | None,
    espn_player_ids: set[str],
    sleeper_player_ids: set[str],
) -> list[Fixture]:
    """Public NBA data, trimmed to a small sample that still covers the recorded rosters."""
    out: list[Fixture] = []
    if espn_season:
        players = _json(
            espn_public.players_projections(client, espn_season, limit=ESPN_PUBLIC_PLAYERS)
        )
        espn_player_ids |= {str(p["id"]) for p in players.get("players", [])}
        schedule = _json(espn_public.pro_schedule(client, espn_season))
        teams = schedule["settings"]["proTeams"]
        schedule["settings"]["proTeams"] = teams[:SCHEDULE_TEAMS_KEPT]
        out += [
            Fixture("espn_public/players_projections.json", players,
                    f"top {ESPN_PUBLIC_PLAYERS} players by ownership"),
            Fixture("espn_public/pro_schedule.json", schedule,
                    f"trimmed to the first {SCHEDULE_TEAMS_KEPT} of {len(teams)} teams for size"),
        ]  # fmt: skip
    state = _json(sleeper_public.state(client))
    all_players: dict[str, Any] = _json(sleeper_public.players(client))
    keep = {
        pid: p for pid, p in all_players.items()
        if pid in sleeper_player_ids or str(p.get("espn_id")) in espn_player_ids
    }  # fmt: skip
    week_rows = _json(
        sleeper_public.week_projections(client, str(state["season"]), int(state["week"]))
    )
    out += [
        Fixture("sleeper_public/state.json", state),
        Fixture("sleeper_public/players.json", keep,
                f"{len(keep)} of {len(all_players)} players: recorded rosters and ESPN sample"),
        Fixture("sleeper_public/projections_week.json",
                [row for row in week_rows if str(row.get("player_id")) in keep],
                f"week {state['week']} projections for the kept players"),
    ]  # fmt: skip
    return out


def write_fixtures(fixtures: list[Fixture], root: Path, recorded_at: datetime) -> list[Path]:
    written = []
    for fixture in fixtures:
        path = root / fixture.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture.serialized() + "\n", encoding="utf-8")
        written.append(path)
    manifest = {
        "recorded_at": recorded_at.isoformat(),
        "how": "uv run fh fixtures record (redacted; see src/fantasy_helper/adapters/redaction.py)",
        "files": {f.path: f.note for f in fixtures},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return written
