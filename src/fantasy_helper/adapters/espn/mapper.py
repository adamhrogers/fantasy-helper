"""ESPN responses to domain records. Pure functions of the response: no I/O.

Code tables (stat IDs, lineup slot IDs) follow the community `espn-api` library's
`basketball/constant.py`, checked against real responses in the M1 spike.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fantasy_helper.domain.league import (
    AcquisitionLimits,
    CategoryRule,
    LeagueRules,
    LineupMode,
    RosterSlot,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.records import (
    ItemAction,
    MatchupRecord,
    MatchupSide,
    PlayerKey,
    RosterEntryRecord,
    RulesRecord,
    TeamRecord,
    TransactionItem,
    TransactionKind,
    TransactionRecord,
    TransactionStatus,
)
from fantasy_helper.domain.stats import Stat

STATS: dict[int, tuple[str, Stat]] = {
    0: ("PTS", Stat.POINTS),
    1: ("BLK", Stat.BLOCKS),
    2: ("STL", Stat.STEALS),
    3: ("AST", Stat.ASSISTS),
    4: ("OREB", Stat.OFFENSIVE_REBOUNDS),
    5: ("DREB", Stat.DEFENSIVE_REBOUNDS),
    6: ("REB", Stat.REBOUNDS),
    9: ("PF", Stat.PERSONAL_FOULS),
    11: ("TO", Stat.TURNOVERS),
    13: ("FGM", Stat.FIELD_GOALS_MADE),
    14: ("FGA", Stat.FIELD_GOALS_ATTEMPTED),
    15: ("FTM", Stat.FREE_THROWS_MADE),
    16: ("FTA", Stat.FREE_THROWS_ATTEMPTED),
    17: ("3PM", Stat.THREES_MADE),
    18: ("3PA", Stat.THREES_ATTEMPTED),
    37: ("DD", Stat.DOUBLE_DOUBLES),
    38: ("TD", Stat.TRIPLE_DOUBLES),
    40: ("MIN", Stat.MINUTES),
}
RATIOS: dict[int, tuple[str, tuple[Stat, Stat]]] = {
    19: ("FG%", (Stat.FIELD_GOALS_MADE, Stat.FIELD_GOALS_ATTEMPTED)),
    20: ("FT%", (Stat.FREE_THROWS_MADE, Stat.FREE_THROWS_ATTEMPTED)),
    21: ("3P%", (Stat.THREES_MADE, Stat.THREES_ATTEMPTED)),
}
SLOTS: dict[int, str] = {
    0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F",
    7: "SG/SF", 8: "G/F", 9: "PF/C", 10: "F/C", 11: "UTIL", 12: "BENCH", 13: "IR",
}  # fmt: skip
POINTS_TYPES = frozenset({"H2H_POINTS", "POINTS"})
CATEGORY_TYPES = frozenset({"H2H_CATEGORY", "H2H_MOST_CATEGORIES", "ROTO"})
_WINNERS = {"HOME": "home", "AWAY": "away", "TIE": "tie"}
_KINDS = {
    "FREEAGENT": TransactionKind.FREE_AGENT,
    "WAIVER": TransactionKind.WAIVER,
    "WAIVER_ERROR": TransactionKind.WAIVER,
}
_TRADE_TYPES = frozenset(
    {"TRADE_PROPOSAL", "TRADE_ACCEPT", "TRADE_DECLINE", "TRADE_UPHOLD", "TRADE_VETO"}
)


class EspnMappingError(Exception):
    pass


@dataclass(frozen=True)
class LeagueStatus:
    latest_scoring_period: int
    current_matchup_period: int
    first_scoring_period: int
    final_scoring_period: int


def _from_ms(ms: Any) -> datetime | None:
    return datetime.fromtimestamp(int(ms) / 1000, UTC) if ms else None


def _team_name(team: dict[str, Any]) -> str:
    if team.get("name"):
        return str(team["name"])
    return f"{team.get('location', '')} {team.get('nickname', '')}".strip() or str(team["id"])


def league_status(data: Any) -> LeagueStatus:
    status = data.get("status") or {}
    try:
        return LeagueStatus(
            latest_scoring_period=int(status["latestScoringPeriod"]),
            current_matchup_period=int(status["currentMatchupPeriod"]),
            first_scoring_period=int(status["firstScoringPeriod"]),
            final_scoring_period=int(status["finalScoringPeriod"]),
        )
    except KeyError as exc:
        raise EspnMappingError(f"league status is missing {exc}") from exc


def map_rules(data: Any) -> RulesRecord:
    """Settings (views mSettings + mStatus) to league rules."""
    settings = data["settings"]
    scoring_settings = settings["scoringSettings"]
    scoring_type = scoring_settings.get("scoringType")
    items = scoring_settings.get("scoringItems", [])
    unmapped: list[str] = []

    if scoring_type in POINTS_TYPES:
        weights = {}
        for item in items:
            stat_id, points = int(item["statId"]), float(item.get("points") or 0)
            if stat_id in STATS:
                weights[STATS[stat_id][1]] = points
            elif points:
                unmapped.append(f"statId {stat_id} ({points:+g})")
        scoring = ScoringRules(scoring_type=ScoringType.POINTS, weights=weights)
    elif scoring_type in CATEGORY_TYPES:
        categories = []
        for item in items:
            stat_id, higher = int(item["statId"]), not item.get("isReverseItem", False)
            if stat_id in STATS:
                name, stat = STATS[stat_id]
                categories.append(CategoryRule(name=name, stat=stat, higher_is_better=higher))
            elif stat_id in RATIOS:
                name, ratio = RATIOS[stat_id]
                categories.append(CategoryRule(name=name, ratio_of=ratio, higher_is_better=higher))
            else:
                unmapped.append(f"statId {stat_id}")
        scoring = ScoringRules(scoring_type=ScoringType.CATEGORIES, categories=tuple(categories))
    else:
        raise EspnMappingError(f"unsupported ESPN scoring type {scoring_type!r}")

    roster = settings["rosterSettings"]
    slots = tuple(
        RosterSlot.named(SLOTS[int(slot_id)], int(count))
        for slot_id, count in sorted(
            roster.get("lineupSlotCounts", {}).items(), key=lambda kv: int(kv[0])
        )
        if int(count) > 0 and int(slot_id) in SLOTS
    )
    lock = str(roster.get("lineupLocktimeType", ""))
    return RulesRecord(
        rules=LeagueRules(
            team_count=int(settings["size"]),
            scoring=scoring,
            lineup_mode=LineupMode.WEEKLY if "WEEK" in lock else LineupMode.DAILY,
            roster_slots=slots,
            acquisition_limits=_acquisition_limits(settings.get("acquisitionSettings") or {}),
        ),
        unmapped_scoring=tuple(unmapped),
    )


def _acquisition_limits(acq: dict[str, Any]) -> AcquisitionLimits:
    season = int(acq.get("acquisitionLimit", -1))
    matchup = float(acq.get("matchupAcquisitionLimit", -1))
    per_day = bool(acq.get("matchupLimitPerScoringPeriod"))
    return AcquisitionLimits(
        per_season=season if season > 0 else None,
        per_matchup=int(matchup) if matchup > 0 and not per_day else None,
        per_matchup_per_day=matchup if matchup > 0 and per_day else None,
    )


def map_rosters(data: Any) -> tuple[list[TeamRecord], list[RosterEntryRecord]]:
    """Teams and rosters (views mTeam + mRoster)."""
    teams, entries = [], []
    for team in data.get("teams", []):
        team_id = str(team["id"])
        teams.append(TeamRecord(external_id=team_id, name=_team_name(team)))
        for entry in team.get("roster", {}).get("entries", []):
            slot = SLOTS.get(int(entry.get("lineupSlotId", -1)))
            if slot is None:
                raise EspnMappingError(f"unknown lineup slot {entry.get('lineupSlotId')}")
            entries.append(
                RosterEntryRecord(
                    team_external_id=team_id,
                    player=PlayerKey(source=IdSource.ESPN, external_id=str(entry["playerId"])),
                    slot=slot,
                    acquired_at=_from_ms(entry.get("acquisitionDate")),
                    acquisition_type=entry.get("acquisitionType"),
                )
            )
    return teams, entries


def map_matchups(data: Any) -> list[MatchupRecord]:
    """Every matchup of the season (views mMatchupScore + mScoreboard)."""

    def side(raw: dict[str, Any]) -> MatchupSide:
        return MatchupSide(
            team_external_id=str(raw["teamId"]),
            total_points=raw.get("totalPoints"),
            points_by_period={
                int(k): float(v) for k, v in (raw.get("pointsByScoringPeriod") or {}).items()
            },
        )

    return [
        MatchupRecord(
            external_id=str(m["id"]),
            period=int(m["matchupPeriodId"]),
            home=side(m["home"]),
            away=side(m["away"]) if m.get("away") else None,
            winner=_WINNERS.get(str(m.get("winner"))),
        )
        for m in data.get("schedule", [])
    ]


def map_transactions(responses: list[Any]) -> tuple[list[TransactionRecord], int]:
    """Transactions from several per-day responses (view mTransactions2).

    An accepted trade (TRADE_ACCEPT) carries no items: its players are on the proposal it
    points to through `relatedTransactionId`. Executed trades get the accept's ID and timing
    with the proposal's items. Returns (records, executed trades whose proposal wasn't found).
    """
    raw = [t for response in responses for t in response.get("transactions", [])]
    by_id = {t["id"]: t for t in raw}
    records, unresolved = [], 0
    for t in raw:
        kind, status, items = _classify(t)
        if t.get("type") in ("TRADE_ACCEPT", "TRADE_UPHOLD") and t.get("status") == "EXECUTED":
            proposal = by_id.get(t.get("relatedTransactionId"))
            if proposal is None:
                unresolved += 1
            else:
                items = _items(proposal)
        records.append(
            TransactionRecord(
                external_id=str(t["id"]),
                kind=kind,
                status=status,
                team_external_id=str(t["teamId"]) if t.get("teamId") is not None else None,
                proposed_at=_from_ms(t.get("proposedDate")),
                scoring_period=t.get("scoringPeriodId"),
                items=items,
            )
        )
    return records, unresolved


def _classify(
    t: dict[str, Any],
) -> tuple[TransactionKind, TransactionStatus, tuple[TransactionItem, ...]]:
    kind_name, status_name = t.get("type", ""), t.get("status", "")
    if kind_name in _TRADE_TYPES:
        status = {
            "TRADE_PROPOSAL": TransactionStatus.CANCELED
            if status_name == "CANCELED"
            else TransactionStatus.PROPOSED,
            "TRADE_ACCEPT": TransactionStatus.EXECUTED,
            "TRADE_UPHOLD": TransactionStatus.EXECUTED,
            "TRADE_DECLINE": TransactionStatus.DECLINED,
            "TRADE_VETO": TransactionStatus.VETOED,
        }[kind_name]
        if kind_name in ("TRADE_ACCEPT", "TRADE_UPHOLD") and status_name != "EXECUTED":
            status = TransactionStatus.PENDING
        return TransactionKind.TRADE, status, _items(t)
    status = {
        "EXECUTED": TransactionStatus.EXECUTED,
        "CANCELED": TransactionStatus.CANCELED,
        "PENDING": TransactionStatus.PENDING,
        "FAILED_INVALIDPLAYERSOURCE": TransactionStatus.FAILED,
    }.get(
        status_name,
        TransactionStatus.FAILED if kind_name == "WAIVER_ERROR" else TransactionStatus.PENDING,
    )
    return _KINDS.get(kind_name, TransactionKind.OTHER), status, _items(t)


def _items(t: dict[str, Any]) -> tuple[TransactionItem, ...]:
    def team(team_id: Any) -> str | None:
        return str(team_id) if team_id not in (None, 0, -1) else None

    actions = {"ADD": ItemAction.ADD, "DROP": ItemAction.DROP, "TRADE": ItemAction.TRADE}
    return tuple(
        TransactionItem(
            player=PlayerKey(source=IdSource.ESPN, external_id=str(item["playerId"])),
            action=actions[item["type"]],
            from_team=team(item.get("fromTeamId")),
            to_team=team(item.get("toTeamId")),
        )
        for item in t.get("items", [])
        if item.get("type") in actions
    )
