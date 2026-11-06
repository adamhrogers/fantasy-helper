"""Sleeper responses to domain records. Pure functions of the responses: no I/O."""

import re
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from fantasy_helper.domain.league import (
    AcquisitionLimits,
    LeagueRules,
    LineupMode,
    RosterSlot,
    ScoringBonus,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.player import NBA_TEAMS, IdSource, Position
from fantasy_helper.domain.projections import InjuryStatus
from fantasy_helper.domain.records import (
    InjuryRecord,
    ItemAction,
    MatchupRecord,
    MatchupSide,
    PlayerKey,
    ProjectionRecord,
    RosterEntryRecord,
    RulesRecord,
    SourcePlayer,
    TeamRecord,
    TransactionItem,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)
from fantasy_helper.domain.stats import Stat

STATS: dict[str, Stat] = {
    "pts": Stat.POINTS,
    "reb": Stat.REBOUNDS,
    "oreb": Stat.OFFENSIVE_REBOUNDS,
    "dreb": Stat.DEFENSIVE_REBOUNDS,
    "ast": Stat.ASSISTS,
    "stl": Stat.STEALS,
    "blk": Stat.BLOCKS,
    "to": Stat.TURNOVERS,
    "fgm": Stat.FIELD_GOALS_MADE,
    "fga": Stat.FIELD_GOALS_ATTEMPTED,
    "ftm": Stat.FREE_THROWS_MADE,
    "fta": Stat.FREE_THROWS_ATTEMPTED,
    "tpm": Stat.THREES_MADE,
    "tpa": Stat.THREES_ATTEMPTED,
    "pf": Stat.PERSONAL_FOULS,
    "dd": Stat.DOUBLE_DOUBLES,
    "td": Stat.TRIPLE_DOUBLES,
}
_BONUS = re.compile(r"bonus_(pt|reb|ast|stl|blk)_(\d+)p")
_BONUS_STATS = {
    "pt": Stat.POINTS,
    "reb": Stat.REBOUNDS,
    "ast": Stat.ASSISTS,
    "stl": Stat.STEALS,
    "blk": Stat.BLOCKS,
}
SLOTS = {
    "PG": "PG", "SG": "SG", "SF": "SF", "PF": "PF", "C": "C", "G": "G", "F": "F",
    "UTIL": "UTIL", "BN": "BENCH", "IR": "IR",
}  # fmt: skip
_INJURY: dict[str, InjuryStatus] = {
    "out": InjuryStatus.OUT,
    "gtd": InjuryStatus.DAY_TO_DAY,
    "dtd": InjuryStatus.DAY_TO_DAY,
    "questionable": InjuryStatus.DAY_TO_DAY,
    "doubtful": InjuryStatus.DAY_TO_DAY,
    "probable": InjuryStatus.DAY_TO_DAY,
    "sus": InjuryStatus.SUSPENDED,
    "suspended": InjuryStatus.SUSPENDED,
}
LOCK_IN_GAME_MODE = 1  # observed on the lock-in test league (M1 spike, Q4)
_KINDS = {
    "free_agent": TransactionKind.FREE_AGENT,
    "waiver": TransactionKind.WAIVER,
    "trade": TransactionKind.TRADE,
    "commissioner": TransactionKind.COMMISSIONER,
}
_STATUSES = {"complete": TransactionStatus.EXECUTED, "failed": TransactionStatus.FAILED}


class SleeperMappingError(Exception):
    pass


def _key(player_id: Any) -> PlayerKey:
    return PlayerKey(source=IdSource.SLEEPER, external_id=str(player_id))


def _from_ms(ms: Any) -> datetime | None:
    return datetime.fromtimestamp(int(ms) / 1000, UTC) if ms else None


def map_rules(league: Any) -> RulesRecord:
    weights: dict[Stat, float] = {}
    bonuses: list[ScoringBonus] = []
    unmapped: list[str] = []
    for key, value in sorted((league.get("scoring_settings") or {}).items()):
        points = float(value or 0)
        if key in STATS:
            if points:
                weights[STATS[key]] = points
        elif match := _BONUS.fullmatch(key):
            if points:
                stat = _BONUS_STATS[match.group(1)]
                bonuses.append(
                    ScoringBonus(stat=stat, threshold=float(match.group(2)), points=points)
                )
        elif points:
            unmapped.append(f"{key} ({points:+g})")
    if not weights:
        raise SleeperMappingError(
            "no points scoring settings (categories leagues aren't supported yet)"
        )

    settings = league.get("settings") or {}
    unknown = [p for p in league.get("roster_positions", []) if p not in SLOTS]
    if unknown:
        raise SleeperMappingError(f"unknown roster positions {sorted(set(unknown))}")
    counts = Counter(SLOTS[p] for p in league.get("roster_positions", []))
    if settings.get("reserve_slots"):
        counts["IR"] += int(settings["reserve_slots"])
    team_count = league.get("total_rosters") or settings.get("num_teams")
    if not team_count:
        raise SleeperMappingError("league has no team count")
    order = list(SLOTS.values())
    return RulesRecord(
        rules=LeagueRules(
            team_count=int(team_count),
            scoring=ScoringRules(
                scoring_type=ScoringType.POINTS, weights=weights, bonuses=tuple(bonuses)
            ),
            lineup_mode=LineupMode.LOCK_IN
            if settings.get("game_mode") == LOCK_IN_GAME_MODE
            else LineupMode.DAILY,
            roster_slots=tuple(
                RosterSlot.named(slot, count)
                for slot, count in sorted(counts.items(), key=lambda kv: order.index(kv[0]))
            ),
            acquisition_limits=AcquisitionLimits(),
        ),
        unmapped_scoring=tuple(unmapped),
    )


def map_rosters(
    league: Any, rosters: Any, users: Any
) -> tuple[list[TeamRecord], list[RosterEntryRecord]]:
    """Teams and rosters. `starters` lines up with the starting slots in roster_positions."""
    names = {
        u.get("user_id"): (u.get("metadata") or {}).get("team_name") or u.get("display_name")
        for u in users
    }
    starting_slots = [SLOTS[p] for p in league.get("roster_positions", []) if p != "BN"]
    teams, entries = [], []
    for roster in rosters:
        team_id = str(roster["roster_id"])
        teams.append(
            TeamRecord(
                external_id=team_id, name=names.get(roster.get("owner_id")) or f"Team {team_id}"
            )
        )
        placed: set[str] = set()
        for slot, player in zip(starting_slots, roster.get("starters") or [], strict=False):
            if player and player != "0":
                entries.append(
                    RosterEntryRecord(team_external_id=team_id, player=_key(player), slot=slot)
                )
                placed.add(str(player))
        for player in roster.get("reserve") or []:
            entries.append(
                RosterEntryRecord(team_external_id=team_id, player=_key(player), slot="IR")
            )
            placed.add(str(player))
        for player in roster.get("players") or []:
            if str(player) not in placed and str(player) not in {
                str(t) for t in roster.get("taxi") or []
            }:
                entries.append(
                    RosterEntryRecord(team_external_id=team_id, player=_key(player), slot="BENCH")
                )
    return teams, entries


def map_matchups(week: int, data: Any) -> list[MatchupRecord]:
    """One week's matchups. Sleeper pairs teams by matchup_id; points are per player, per week."""
    pairs: dict[Any, list[dict[str, Any]]] = {}
    for row in data or []:
        if row.get("matchup_id") is not None:
            pairs.setdefault(row["matchup_id"], []).append(row)

    def side(row: dict[str, Any]) -> MatchupSide:
        return MatchupSide(
            team_external_id=str(row["roster_id"]),
            total_points=row.get("points"),
            player_points={str(k): float(v) for k, v in (row.get("players_points") or {}).items()},
        )

    return [
        MatchupRecord(
            external_id=f"{week}-{matchup_id}",
            period=week,
            home=side(rows[0]),
            away=side(rows[1]) if len(rows) > 1 else None,
        )
        for matchup_id, rows in sorted(pairs.items(), key=lambda kv: str(kv[0]))
    ]


def map_transactions(data: Any) -> list[TransactionRecord]:
    records = []
    for t in data or []:
        kind = _KINDS.get(t.get("type"), TransactionKind.OTHER)
        adds = {str(k): str(v) for k, v in (t.get("adds") or {}).items()}
        drops = {str(k): str(v) for k, v in (t.get("drops") or {}).items()}
        if kind is TransactionKind.TRADE:
            items = [
                TransactionItem(
                    player=_key(p), action=ItemAction.TRADE, from_team=drops.get(p), to_team=to
                )
                for p, to in adds.items()
            ]
        else:
            items = [
                TransactionItem(player=_key(p), action=ItemAction.ADD, to_team=to)
                for p, to in adds.items()
            ]
            items += [
                TransactionItem(player=_key(p), action=ItemAction.DROP, from_team=fr)
                for p, fr in drops.items()
            ]
        roster_ids = t.get("roster_ids") or []
        records.append(
            TransactionRecord(
                external_id=str(t["transaction_id"]),
                kind=kind,
                status=_STATUSES.get(t.get("status"), TransactionStatus.PENDING),
                team_external_id=str(roster_ids[0]) if roster_ids else None,
                proposed_at=_from_ms(t.get("created")),
                scoring_period=t.get("leg"),
                items=tuple(items),
            )
        )
    return records


# ---------------------------------------------------------------- players and projections


def _positions(player: dict[str, Any]) -> tuple[Position, ...]:
    names = player.get("fantasy_positions") or [player.get("position")]
    return tuple(sorted({Position(p) for p in names if p in Position.__members__}))


def map_players(players: Any, *, include: set[str] | None = None) -> list[SourcePlayer]:
    """Active players, plus any in `include` (rostered players Sleeper marks inactive)."""
    keep = include or set()
    return [
        SourcePlayer(
            key=_key(pid),
            full_name=p.get("full_name")
            or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            nba_team=p.get("team") if p.get("team") in NBA_TEAMS else None,
            positions=_positions(p),
            active=bool(p.get("active")),
        )
        for pid, p in players.items()
        if (p.get("active") or pid in keep)
        and (p.get("full_name") or p.get("last_name"))
        and p.get("position") != "DEF"
    ]


def map_injuries(players: Any) -> list[InjuryRecord]:
    return [
        InjuryRecord(
            player=_key(pid),
            status=_INJURY.get(str(p.get("injury_status") or "").lower(), InjuryStatus.ACTIVE),
            note=p.get("injury_notes") or p.get("injury_body_part"),
        )
        for pid, p in players.items()
        if p.get("active")
    ]


def map_projections(rows: Any, *, per_game_rows: bool) -> list[ProjectionRecord]:
    """Season projections (already per-game averages) or weekly rows (one game each).

    A weekly row with no stats means the player isn't expected to play that game (usually
    injured). It's kept as a projection of 0 games, which streaming and lock-in decisions need.
    """
    records = []
    for row in rows or []:
        stats = row.get("stats") or {}
        per_game = {STATS[k]: float(v) for k, v in stats.items() if k in STATS and v is not None}
        if not per_game and not per_game_rows:
            continue
        records.append(
            ProjectionRecord(
                player=_key(row["player_id"]),
                per_game=per_game,
                games=(1.0 if per_game else 0.0) if per_game_rows else None,
                game_date=date.fromisoformat(row["date"])
                if per_game_rows and row.get("date")
                else None,
            )
        )
    return records
