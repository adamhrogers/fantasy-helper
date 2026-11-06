"""Redaction for recorded fixtures (ADR 0012): the repo is public, leaguemates aren't.

Replaced with stable pseudonyms: members' names and IDs (ESPN SWIDs, Sleeper user IDs), team
names, league names and IDs, avatars and chat text. NBA player data is public and kept.

The Redactor remembers every original value it replaced. `leaks()` then searches the
serialized output for all of them, so a field this module doesn't know about can't slip
through unnoticed: the recorder refuses to write anything that still contains one.
"""

import copy
import re
from collections.abc import Iterable
from typing import Any

SWID_PATTERN = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
)
FIXTURE_ESPN_LEAGUE_ID = 1000001
FIXTURE_SLEEPER_LEAGUE_ID = "900000000000000001"
MIN_TRACKED_LENGTH = 4  # shorter values (2-letter abbreviations) are replaced but not searched
# Values shorter than this only count as leaks when they stand alone as a word: a 4-letter
# team abbreviation would otherwise match inside ESPN field names like "isAutoReactivate".
WHOLE_WORD_BELOW = 8

_SLEEPER_LEAGUE_NULLED = (
    "avatar", "last_author_avatar", "last_author_display_name", "last_author_id",
    "last_author_is_bot", "last_message_attachment", "last_message_id", "last_message_text_map",
    "last_message_time", "last_pinned_message_id", "last_read_id", "previous_league_id",
    "bracket_id", "bracket_overrides_id", "loser_bracket_id", "loser_bracket_overrides_id",
    "group_id", "draft_id", "company_id", "shard",
)  # fmt: skip


class RedactionLeakError(Exception):
    """Identifying values would have been written. The message never includes the values."""


class Redactor:
    """Stable pseudonyms within one recording, plus the originals they replaced, by kind."""

    def __init__(self) -> None:
        self._numbers: dict[tuple[str, str], int] = {}
        self._originals: dict[str, str] = {}  # original value -> kind

    def number(self, kind: str, original: object) -> int:
        key = (kind, str(original).strip().strip("{}").lower())
        if key not in self._numbers:
            self._numbers[key] = 1 + sum(1 for k in self._numbers if k[0] == kind)
        return self._numbers[key]

    def track(self, kind: str, *values: object) -> None:
        for value in values:
            if isinstance(value, str | int) and len(str(value).strip()) >= MIN_TRACKED_LENGTH:
                self._originals.setdefault(str(value).strip(), kind)

    def swid(self, original: str) -> str:
        self.track("member id", original, original.strip("{}"))
        return f"{{00000000-0000-0000-0000-{self.number('swid', original):012d}}}"

    def scrub_swids(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: self.scrub_swids(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.scrub_swids(v) for v in data]
        if isinstance(data, str) and SWID_PATTERN.fullmatch(data):
            return self.swid(data)
        return data

    def leaks(self, text: str, extra: Iterable[str] = ()) -> list[str]:
        """Kinds of original values still present in `text` (empty means clean)."""
        found = {kind for value, kind in self._originals.items() if _appears(text, value)}
        for value in extra:
            if value and _contains_token(text, value):
                found.add("credential or league id")
        return sorted(found)


def _appears(text: str, value: str) -> bool:
    if len(value) < WHOLE_WORD_BELOW:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return value.lower() in text.lower()


def _contains_token(text: str, value: str) -> bool:
    if value.isdigit():  # league IDs: don't match inside longer numbers
        return re.search(rf"(?<!\d){re.escape(value)}(?!\d)", text) is not None
    return value.lower() in text.lower()


def redact_espn(data: Any, r: Redactor, *, league_id: str) -> Any:
    data = copy.deepcopy(data)
    if not isinstance(data, dict):
        return r.scrub_swids(data)
    if str(data.get("id")) == str(league_id):
        data["id"] = FIXTURE_ESPN_LEAGUE_ID
    settings = data.get("settings")
    if isinstance(settings, dict) and "name" in settings:
        r.track("league name", settings["name"])
        settings["name"] = "ESPN Fixture League"
    for member in data.get("members") or []:
        # Track what identifies a person: the full name and the display name. A first or
        # last name alone also matches public NBA player names, so it isn't searched for.
        first, last = member.get("firstName") or "", member.get("lastName") or ""
        r.track("member name", member.get("displayName"), f"{first} {last}".strip())
        n = r.number("member", member.get("id"))
        member.update(displayName=f"member{n}", firstName="Member", lastName=str(n))
        member.pop("notificationSettings", None)
    for team in data.get("teams") or []:
        r.track(
            "team name",
            team.get("name"),
            team.get("abbrev"),
            team.get("location"),
            team.get("nickname"),
        )
        tid = team.get("id")
        replacements = {
            "name": f"Team {tid}",
            "abbrev": f"T{tid}",
            "location": "Team",
            "nickname": str(tid),
            "logo": "",
        }
        team.update({field: value for field, value in replacements.items() if field in team})
    return r.scrub_swids(data)


def redact_sleeper(dataset: str, data: Any, r: Redactor) -> Any:
    data = copy.deepcopy(data)

    def user(uid: object) -> str | None:
        if not uid:
            return None
        r.track("member id", str(uid))
        return f"8{r.number('sleeper user', uid):017d}"

    if dataset == "league" and isinstance(data, dict):
        r.track("league name", data.get("name"), data.get("last_author_display_name"))
        r.track("league id", data.get("league_id"), data.get("previous_league_id"))
        data["name"] = "Sleeper Fixture League"
        data["league_id"] = FIXTURE_SLEEPER_LEAGUE_ID
        for field in _SLEEPER_LEAGUE_NULLED:
            if field in data:
                data[field] = None
        data["metadata"] = {}
    elif dataset == "users":
        for member in data:
            meta = member.get("metadata") or {}
            r.track("member name", member.get("display_name"), meta.get("team_name"))
            n = r.number("sleeper user", member.get("user_id"))
            member.update(
                user_id=user(member.get("user_id")),
                display_name=f"member{n}",
                avatar=None,
                metadata={"team_name": f"Team {n}"},
                league_id=FIXTURE_SLEEPER_LEAGUE_ID,
                settings=None,
            )
    elif dataset == "rosters":
        for roster in data:
            roster["owner_id"] = user(roster.get("owner_id"))
            roster["co_owners"] = [user(c) for c in roster.get("co_owners") or []] or None
            roster["league_id"] = FIXTURE_SLEEPER_LEAGUE_ID
            roster.pop("player_map", None)
    elif dataset.startswith("transactions"):
        for transaction in data:
            transaction["creator"] = user(transaction.get("creator"))
            transaction["metadata"] = None
    return data
