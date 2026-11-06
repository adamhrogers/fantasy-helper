"""Deciding which canonical player a Sleeper player is. Pure: no database, no I/O.

Sleeper fills no ESPN IDs for NBA players (M1 spike), so links are made from player facts,
only when the match is unambiguous, in this order (ADR 0018):

1. override: a manual link from `identity.overrides`
2. name_birthdate: same normalized name and a birth date within a day. It doesn't depend on
   the team, so it survives trades even for common names. The one-day window absorbs a
   time-zone artifact in the two platforms' birth dates.
3. name_team: same name and same NBA team, exactly one candidate
4. name_unique: same name, the only one with it on both platforms

Birth dates are evidence, never a veto: on 2026-11-06, 5 of 623 correct links had birth dates
that disagreed by more than a day because one platform's data was wrong.

Once made, a link is stored by platform IDs, so later trades and name changes don't touch it.
Anything ambiguous stays unmatched and is reported, never guessed.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fantasy_helper.domain.records import SourcePlayer
from fantasy_helper.identity.names import normalize_name

BIRTH_DATE_TOLERANCE_DAYS = 1


@dataclass(frozen=True)
class Candidate:
    player_id: UUID
    name: str  # normalized
    team: str | None
    birth_date: date | None = None


def birth_dates_agree(a: date | None, b: date | None) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= BIRTH_DATE_TOLERANCE_DAYS


class Matcher:
    def __init__(
        self,
        candidates: Iterable[Candidate],
        sleeper_players: Iterable[SourcePlayer],
        overrides: Mapping[str, UUID],
        claimed: set[UUID],
    ) -> None:
        self._by_name: dict[str, list[Candidate]] = defaultdict(list)
        for candidate in candidates:
            self._by_name[candidate.name].append(candidate)
        self._sleeper_names = Counter(normalize_name(p.full_name) for p in sleeper_players)
        self._overrides = overrides
        self.claimed = claimed  # canonical players already linked to a Sleeper ID

    def match(self, player: SourcePlayer) -> tuple[UUID, str] | None:
        if player.key.external_id in self._overrides:
            return self._overrides[player.key.external_id], "override"
        name = normalize_name(player.full_name)
        same_name = self._by_name.get(name, [])
        open_ = [c for c in same_name if c.player_id not in self.claimed]

        by_birth = [c for c in open_ if birth_dates_agree(c.birth_date, player.birth_date)]
        if len(by_birth) == 1:
            return by_birth[0].player_id, "name_birthdate"
        by_team = [c for c in open_ if player.nba_team and c.team == player.nba_team]
        if len(by_team) == 1:
            return by_team[0].player_id, "name_team"
        if len(same_name) == 1 and len(open_) == 1 and self._sleeper_names[name] == 1:
            return open_[0].player_id, "name_unique"
        return None
