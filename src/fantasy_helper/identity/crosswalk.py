"""The identity crosswalk: one canonical player per NBA player, linked to each platform's ID.

ESPN players become canonical players (ESPN covers both of the owner's main leagues and gives
the display fields). Sleeper players link to them through `identity.matching`; one that
can't be matched safely gets its own provisional canonical player, is reported, and is
folded in later if an override or a better match appears.
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.records import PlayerKey, SourcePlayer
from fantasy_helper.identity import overrides as manual
from fantasy_helper.identity.matching import Candidate, Matcher
from fantasy_helper.identity.names import normalize_name
from fantasy_helper.storage.repositories import players as repo

UNMATCHED = "unmatched"


@dataclass
class CrosswalkReport:
    created: int = 0
    linked: Counter[str] = field(default_factory=Counter)
    unmatched: list[SourcePlayer] = field(default_factory=list)


def sync_players(
    session: Session,
    espn: list[SourcePlayer],
    sleeper: list[SourcePlayer],
    overrides: dict[str, str] | None = None,
) -> CrosswalkReport:
    report = CrosswalkReport()
    links = repo.load_links(session)
    rows = repo.load_players(session)

    new_players: list[tuple[UUID, SourcePlayer]] = []
    new_links: list[tuple[UUID, PlayerKey, str]] = []
    for player in _unique(espn):
        link = links.get((IdSource.ESPN.value, player.key.external_id))
        if link is None:
            player_id = uuid.uuid4()
            new_players.append((player_id, player))
            new_links.append((player_id, player.key, "primary"))
            links[(IdSource.ESPN.value, player.key.external_id)] = repo.Link(player_id, "primary")
            report.created += 1
        elif (row := rows.get(link.player_id)) is not None:
            row.full_name, row.nba_team = player.full_name, player.nba_team
            row.positions = [p.value for p in player.positions]
    session.flush()
    # Players before the links that point to them, in bulk (one round trip each).
    repo.insert_players(session, new_players)
    repo.insert_links(session, new_links)
    new_players, new_links = [], []

    espn_ids = {k: v.player_id for k, v in links.items() if k[0] == IdSource.ESPN.value}
    by_espn_id = {player.key.external_id: player for player in espn}
    candidates = [
        Candidate(pid, normalize_name(by_espn_id[ext].full_name), by_espn_id[ext].nba_team)
        for (_, ext), pid in espn_ids.items()
        if ext in by_espn_id
    ]
    claimed = {
        link.player_id
        for (source, _), link in links.items()
        if source == IdSource.SLEEPER.value and link.method != UNMATCHED
    }
    resolved_overrides = {
        sleeper_id: espn_ids[(IdSource.ESPN.value, espn_id)]
        for sleeper_id, espn_id in (
            manual.SLEEPER_TO_ESPN if overrides is None else overrides
        ).items()
        if (IdSource.ESPN.value, espn_id) in espn_ids
    }
    sleeper_players = _unique(sleeper)
    matcher = Matcher(candidates, sleeper_players, resolved_overrides, claimed)

    for player in sleeper_players:
        key = (IdSource.SLEEPER.value, player.key.external_id)
        link = links.get(key)
        if link is not None and link.method != UNMATCHED:
            continue
        match = matcher.match(player)
        if match is None:
            if link is None:
                player_id = uuid.uuid4()
                new_players.append((player_id, player))
                new_links.append((player_id, player.key, UNMATCHED))
                links[key] = repo.Link(player_id, UNMATCHED)
            report.unmatched.append(player)
            continue
        target, method = match
        if link is None:
            new_links.append((target, player.key, method))
        else:
            repo.merge(session, link.player_id, target, key=player.key, method=method)
        links[key] = repo.Link(target, method)
        matcher.claimed.add(target)
        report.linked[method] += 1
    repo.insert_players(session, new_players)
    repo.insert_links(session, new_links)
    session.flush()
    return report


class Resolver:
    """Platform key -> canonical player ID, loaded once per run."""

    def __init__(self, session: Session) -> None:
        self._links = repo.load_links(session)
        self._has_espn = {
            link.player_id
            for (source, _), link in self._links.items()
            if source == IdSource.ESPN.value
        }

    def resolve(self, key: PlayerKey) -> UUID | None:
        link = self._links.get((key.source.value, key.external_id))
        return link.player_id if link else None

    def linked_to_espn(self, key: PlayerKey) -> bool:
        """Whether this player's canonical identity includes an ESPN ID."""
        player_id = self.resolve(key)
        return player_id is not None and player_id in self._has_espn


def _unique(players: list[SourcePlayer]) -> list[SourcePlayer]:
    seen: dict[str, SourcePlayer] = {}
    for player in players:
        seen.setdefault(player.key.external_id, player)
    return list(seen.values())
