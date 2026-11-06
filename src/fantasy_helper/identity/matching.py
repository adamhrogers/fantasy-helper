"""Deciding which canonical player a Sleeper player is. Pure: no database, no I/O.

Sleeper fills no ESPN IDs for NBA players (M1 spike), so links are made by name, and only
when the match is unambiguous:

1. override: a manual link from `identity.overrides`
2. name_team: same normalized name and same NBA team, exactly one candidate
3. name_unique: same normalized name, the only one with that name on both platforms
   (covers players who changed teams)

Anything else stays unmatched and is reported, never guessed.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from fantasy_helper.domain.records import SourcePlayer
from fantasy_helper.identity.names import normalize_name


@dataclass(frozen=True)
class Candidate:
    player_id: UUID
    name: str  # normalized
    team: str | None


class Matcher:
    def __init__(
        self,
        candidates: Iterable[Candidate],
        sleeper_players: Iterable[SourcePlayer],
        overrides: Mapping[str, UUID],
        claimed: set[UUID],
    ) -> None:
        self._by_name_team: dict[tuple[str, str | None], list[UUID]] = defaultdict(list)
        self._by_name: dict[str, list[UUID]] = defaultdict(list)
        for c in candidates:
            self._by_name_team[(c.name, c.team)].append(c.player_id)
            self._by_name[c.name].append(c.player_id)
        self._sleeper_names = Counter(normalize_name(p.full_name) for p in sleeper_players)
        self._overrides = overrides
        self.claimed = claimed  # canonical players already linked to a Sleeper ID

    def match(self, player: SourcePlayer) -> tuple[UUID, str] | None:
        if player.key.external_id in self._overrides:
            return self._overrides[player.key.external_id], "override"
        name = normalize_name(player.full_name)
        same_team = [
            p for p in self._by_name_team.get((name, player.nba_team), []) if p not in self.claimed
        ]
        if player.nba_team and len(same_team) == 1:
            return same_team[0], "name_team"
        same_name = self._by_name.get(name, [])
        if (
            len(same_name) == 1
            and self._sleeper_names[name] == 1
            and same_name[0] not in self.claimed
        ):
            return same_name[0], "name_unique"
        return None
