"""The daily "what happened" cards: last night in one league, in that league's own scoring.

Written by code from a `DailySheet`, no model. Each kind of card is a block of its own, built
from the sheet alone and skipped when it has nothing to say, so a later "customize my report"
can switch blocks on and off by name (`BLOCKS`) without touching the others:

- `matchup`: last night's points for both teams and where the matchup stands, checked against
  the platform's own totals where it gives them
- `my-team`: who on his team beat or missed what was expected of them, who didn't play
- `standouts`: the best games of the night across the league
- `pickups`: recent free-agent and waiver pickups that had a good night
- `surprises`: what was expected against what wasn't, across the league

A card's `id` is `daily-<block>`. The kinds are the ones the app already draws (MATCHUP, RECAP,
LEAGUE_MOVES): a new kind would change the API's schema for no gain. Each player or fact is its
own line in the card's `items` (bullets on the phone), under at most a one-line `body`.
"""

from collections.abc import Callable, Collection, Iterable
from datetime import date

from pydantic import JsonValue

from fantasy_helper.domain.league import NON_STARTING_SLOTS, Platform
from fantasy_helper.domain.reports import Card, CardKind, bullets
from fantasy_helper.engine.recap import (
    GOOD_GAME_QUANTILE,
    MY_TEAM_Z,
    SURPRISE_Z,
    Performance,
    category_standing,
    expected_but_sat,
    pickups_that_did_well,
    standouts,
    surprises,
)
from fantasy_helper.facts.daily import DailySheet, MatchupDay, MatchupMode, Side

STANDOUTS_SHOWN = 5
PICKUPS_SHOWN = 4
SURPRISES_SHOWN = 3
MY_TEAM_SHOWN = 3

_PLATFORM = {Platform.ESPN: "ESPN", Platform.SLEEPER: "Sleeper", Platform.YAHOO: "Yahoo"}


def recap_cards(sheet: DailySheet, *, only: Collection[str] | None = None) -> list[Card]:
    """Every block that has something to say, in order. `only` names the blocks to build."""
    if sheet.games == 0:
        return [_no_games(sheet)]
    cards = []
    for name, block in BLOCKS.items():
        if only is None or name in only:
            card = block(sheet)
            if card is not None:
                cards.append(card)
    if not cards and only is None:
        cards.append(
            Card(
                id="daily-quiet",
                kind=CardKind.RECAP,
                title=f"A quiet night on {_day(sheet)}",
                body="There were games, but nothing worth a card: no standout, pickup or surprise.",
            )
        )
    return cards


# --------------------------------------------------------------------------- helpers


def _day(sheet: DailySheet) -> str:
    return f"{sheet.game_day:%a %b} {sheet.game_day.day}"


def _val(sheet: DailySheet, v: float) -> str:
    """A game's value: league points as 31.5; a categories index with its sign, +3.2."""
    return f"{v:.1f}" if sheet.value.is_points else f"{v:+.1f}"


def _line(sheet: DailySheet, p: Performance) -> str:
    """A categories game's counting stats (a points game is its number already)."""
    if sheet.value.is_points:
        return ""
    parts = [
        f"{p.line[cat.stat]:g} {cat.name}"
        for cat in sheet.rules.scoring.categories
        if cat.stat is not None and cat.higher_is_better and p.line.get(cat.stat, 0.0) > 0
    ][:4]
    return ", ".join(parts)


def _who(sheet: DailySheet, owner: str | None) -> str:
    if owner is None:
        return "free agent"
    if owner == sheet.my_team:
        return "yours"
    return sheet.team_names.get(owner, f"team {owner}").strip()


def _owner(sheet: DailySheet, owner: str | None) -> str | None:
    """Whose player he was last night, or None when that isn't known: with no lineup stored for
    the day, nobody's ownership is shown (today's roster would credit traded or dropped players
    to the wrong team)."""
    return _who(sheet, owner) if sheet.lineup_known else None


def _paren(*parts: str | None) -> str:
    """ " (a, b)" from the parts that are there; nothing when none is."""
    kept = [p for p in parts if p]
    return f" ({', '.join(kept)})" if kept else ""


def _lead(a: float, b: float, name: str) -> str:
    gap = abs(a - b)
    if gap < 0.05:
        return "tied"
    return f"you lead {name} by {gap:.1f}" if a > b else f"{name} leads by {gap:.1f}"


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def _entry(sheet: DailySheet, p: Performance) -> dict[str, JsonValue]:
    """A player's night as plain data, for the app and the next layer to use."""
    return {
        "player_id": str(p.player_id),
        "name": p.name,
        "nba_team": p.nba_team,
        "value": round(p.value, 2),
        "played": p.played,
        "expected": _round(p.expected.mean) if p.expected and p.expected_to_play else None,
        "z": _round(p.z),
        "owner": _owner(sheet, p.owner),
        "slot": p.slot,
        "line": {str(k): round(v, 2) for k, v in p.line.items()},
    }


def _scoring_note(sheet: DailySheet) -> str:
    if sheet.value.is_points:
        note = "Valued in this league's own scoring."
    else:
        note = (
            "This league has no points, so each game is valued as how far its categories sit from "
            "a typical game this season, in standard deviations, turnovers counting against."
        )
    if not sheet.lineup_known:
        note += (
            " Who owned each player isn't shown: the league's lineups for last night weren't saved."
        )
    return note


# --------------------------------------------------------------------------- no games


def _no_games(sheet: DailySheet) -> Card:
    return Card(
        id="daily-no-games",
        kind=CardKind.RECAP,
        title=f"No NBA games on {_day(sheet)}",
        body="There were no games last night, so there is nothing to recap.",
    )


# --------------------------------------------------------------------------- matchup


def _matchup_card(sheet: DailySheet) -> Card | None:
    m = sheet.matchup
    if m is None:
        return None
    platform = _PLATFORM[sheet.league.platform]
    if m.mode is MatchupMode.POINTS:
        title, body, items, reason = _points_matchup(sheet, m, platform)
    elif m.mode is MatchupMode.CATEGORIES:
        title, body, items, reason = _categories_matchup(sheet, m, platform)
    else:
        title, body, items, reason = _lock_in_matchup(m, platform)
    return Card(
        id="daily-matchup",
        kind=CardKind.MATCHUP,
        title=title[:120],
        body=body[:1000],
        items=bullets(items),
        reason=reason,
        data={
            "mode": m.mode.value,
            "week": m.week,
            "final": m.final,
            "game_day": sheet.game_day.isoformat(),
            "me": _side_data(m.me),
            "them": _side_data(m.them),
            "checked": {"matched": m.checked.matched, "total": m.checked.total},
            # A lock-in league's day figure is a game score before locks, never a total.
            "day_points_are": (
                "game scores before locks, not a total"
                if m.mode is MatchupMode.LOCK_IN
                else "the lineup's total"
            ),
        },
    )


def _side_data(s: Side) -> dict[str, JsonValue]:
    return {
        "team": s.team,
        "name": s.name,
        "day_points": _round(s.day_points),
        "week_points": _round(s.week_points),
        "reported_day": _round(s.reported_day),
        "reported_week": _round(s.reported_week),
        "day_categories": {k: round(v, 3) for k, v in s.day_categories.items()},
        "week_categories": (
            None
            if s.week_categories is None
            else {k: round(v, 3) for k, v in s.week_categories.items()}
        ),
        "reported_categories": {k: round(v, 3) for k, v in s.reported_categories.items()},
    }


def _check_note(m: MatchupDay, platform: str) -> list[str]:
    c = m.checked
    if c.total == 0:
        return []
    if c.matched == c.total:
        return [f"Our totals match {platform}'s own ({c.matched} of {c.total})"]
    return [
        f"Our totals differ from {platform}'s own in {c.total - c.matched} of {c.total}: "
        "the saved lineups or box scores may be off"
    ]


_Matchup = tuple[str, str, list[str], str]  # title, body, items, reason


def _points_matchup(sheet: DailySheet, m: MatchupDay, platform: str) -> _Matchup:
    me, them = m.me, m.them
    reason = (
        "Each starter's game from last night, scored by this league's rules, for the lineup "
        f"each team had that day; {platform}'s own daily totals only check it."
    )
    if me.day_points is None or them.day_points is None:
        body = (
            "Last night's lineups weren't saved, so the day's points can't be counted. "
            f"{platform}'s totals are not used in their place."
        )
        return f"Week {m.week} vs {them.name}: last night's points unknown", body, [], reason
    title = f"Last night: you {me.day_points:.1f}, {them.name} {them.day_points:.1f}"
    items = [
        f"Last night ({_day(sheet)}): you {me.day_points:.1f}, {them.name} {them.day_points:.1f}"
    ]
    if me.week_points is not None and them.week_points is not None:
        if m.final:
            result = "you won" if me.week_points > them.week_points else "you lost"
            if me.week_points == them.week_points:
                result = "tied"
            items.append(
                f"Week {m.week} final: you {me.week_points:.1f}, {them.name} "
                f"{them.week_points:.1f} ({result})"
            )
        else:
            items.append(
                f"Week {m.week} so far: you {me.week_points:.1f}, {them.name} "
                f"{them.week_points:.1f} ({_lead(me.week_points, them.week_points, them.name)})"
            )
    else:
        items.append("The week's total isn't known: a day's lineup wasn't saved")
    return title, "", items + _check_note(m, platform), reason


def _categories_matchup(sheet: DailySheet, m: MatchupDay, platform: str) -> _Matchup:
    cats = sheet.rules.scoring.categories
    me, them = m.me, m.them
    reason = (
        "Each team's starters' games for the week so far, from the lineup each had on each day, "
        f"counted category by category; {platform}'s own running totals only check it."
    )
    if me.week_categories is None or them.week_categories is None:
        body = (
            "A day's lineup wasn't saved, so this week's category totals can't be counted. "
            f"{platform}'s totals are not used in their place."
        )
        return f"Week {m.week} vs {them.name}: category totals unknown", body, [], reason
    standing = category_standing(me.week_categories, them.week_categories, cats)
    verb = "Final" if m.final else "So far"
    title = f"Week {m.week} vs {them.name}: {verb.lower()} {standing.record}"
    items = []
    if standing.leading:
        items.append("You lead: " + ", ".join(standing.leading))
    if standing.trailing:
        items.append("You trail: " + ", ".join(standing.trailing))
    if standing.tied:
        items.append("Tied: " + ", ".join(standing.tied))
    body = (
        f"Week {m.week} {verb.lower()} against {them.name} through {_day(sheet)} "
        f"(won-lost-tied: {standing.record})."
    )
    return title, body, items + _check_note(m, platform), reason


def _lock_in_matchup(m: MatchupDay, platform: str) -> _Matchup:
    me, them = m.me, m.them
    reason = (
        "In a lock-in league each starter counts one locked game a week, and the app can't see "
        "locks, so it doesn't add games up into a score. The game scores are last night's games "
        "for the starters in each team's lineup that day, before locks: not a comparable total. "
        f"{platform}'s score is its own number."
    )
    items = []
    if me.day_points is None or them.day_points is None:
        body = "Last night's lineups weren't saved, so last night's games can't be added up."
    else:
        body = "These are game scores before locks, not your score: only a locked game counts."
        items.append(
            f"Last night's games, before locks: you {me.day_points:.1f}, "
            f"{them.name} {them.day_points:.1f}"
        )
    if me.reported_week is not None and them.reported_week is not None:
        items.append(
            f"{platform}'s own score for week {m.week}{'' if m.final else ' so far'}: you "
            f"{me.reported_week:.1f}, {them.name} {them.reported_week:.1f}"
        )
        title = (
            f"Week {m.week} vs {them.name}: {platform} shows {me.reported_week:.1f} to "
            f"{them.reported_week:.1f}"
        )
    else:
        title = f"Week {m.week} vs {them.name}: last night's games"
    return title, body, items, reason


# --------------------------------------------------------------------------- my team


def _my_team_card(sheet: DailySheet) -> Card | None:
    mine = sheet.mine
    if not mine:
        return None
    starters = [p for p in mine if p.slot is not None and p.slot not in NON_STARTING_SLOTS]
    bench = [p for p in mine if p.slot is not None and p.slot in NON_STARTING_SLOTS]
    played = [p for p in starters if p.played]
    beat, missed = surprises(starters, MY_TEAM_SHOWN, min_z=MY_TEAM_Z, rostered_only=True)
    sat = expected_but_sat(starters)
    body = ""
    items: list[str] = []
    title = "Your team last night"
    if sheet.value.is_points:
        compared = [p for p in played if p.expected_to_play]
        if compared:
            actual = sum(p.value for p in compared)
            expected = sum(p.expected.mean for p in compared if p.expected)
            title = f"Your starters: {actual:.1f} against {expected:.1f} expected"
            body = f"Counting the {len(compared)} of your starters with a projection."
    items += [f"Over: {row}" for row in _against_expected(sheet, beat)]
    items += [f"Under: {row}" for row in _against_expected(sheet, missed)]
    items += [f"Didn't play: {p.name} (expected to)" for p in sat]
    if sheet.good_line is not None:
        items += [
            f"Bench: {p.name} {_val(sheet, p.value)}, a good game"
            for p in bench
            if p.played and p.value >= sheet.good_line
        ]
    if not items and not body:
        if not played:
            return None
        body = f"{len(played)} of your starters played, and none was far off what was expected."
    return Card(
        id="daily-my-team",
        kind=CardKind.RECAP,
        title=title,
        body=body[:1000],
        items=bullets(items),
        reason=(
            "Expected is the projection pulled before the games, blended with the player's own "
            f"games this season. {_scoring_note(sheet)}"
        ),
        data={
            "starters": [_entry(sheet, p) for p in starters],
            "bench": [_entry(sheet, p) for p in bench if p.played],
            "lineup_known": sheet.lineup_known,
        },
    )


def _against_expected(sheet: DailySheet, perfs: Iterable[Performance]) -> list[str]:
    return [
        f"{p.name} {_val(sheet, p.value)} (expected {_val(sheet, p.expected.mean)})"
        for p in perfs
        if p.expected is not None
    ]


# --------------------------------------------------------------------------- standouts


def _standouts_card(sheet: DailySheet) -> Card | None:
    top = standouts(sheet.performances, STANDOUTS_SHOWN)
    if not top:
        return None
    rows = []
    for p in top:
        line = _line(sheet, p)
        rows.append(
            f"{p.name} {_val(sheet, p.value)}"
            + (f": {line}" if line else "")
            + _paren(_owner(sheet, p.owner))
        )
    first = top[0]
    return Card(
        id="daily-standouts",
        kind=CardKind.RECAP,
        title=f"Last night's top game: {first.name}, {_val(sheet, first.value)}"[:120],
        body=f"The best games of {_day(sheet)}.",
        items=bullets(rows),
        reason=_scoring_note(sheet),
        data={"standouts": [_entry(sheet, p) for p in top], "unit": sheet.value.unit},
    )


# --------------------------------------------------------------------------- pickups


def _pickups_card(sheet: DailySheet) -> Card | None:
    good = pickups_that_did_well(sheet.pickups, sheet.good_line, PICKUPS_SHOWN)
    if not good or sheet.good_line is None:
        return None
    rows = []
    for pick in good:
        p = pick.performance
        expected = (
            f", expected {_val(sheet, p.expected.mean)}"
            if p.expected and p.expected_to_play
            else ""
        )
        rows.append(
            f"{p.name} {_val(sheet, p.value)}{expected} (added by {_who(sheet, pick.team)} on "
            f"{_short_day(pick.added_day)})"
        )
    first = good[0].performance
    return Card(
        id="daily-pickups",
        kind=CardKind.LEAGUE_MOVES,
        title=f"A recent pickup paid off: {first.name}, {_val(sheet, first.value)}"[:120],
        body="Added in the last week, still on that team last night, and good.",
        items=bullets(rows),
        reason=(
            f"A good game beats {GOOD_GAME_QUANTILE:.0%} of the games played this season "
            f"(at least {_val(sheet, sheet.good_line)} here). {_scoring_note(sheet)}"
        ),
        data={
            "pickups": [
                _entry(sheet, pick.performance)
                | {
                    "added_by": _who(sheet, pick.team),
                    "added_on": pick.added_day,
                    "kind": pick.kind,
                }
                for pick in good
            ],
            "good_line": round(sheet.good_line, 2),
        },
    )


def _short_day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d:%b} {d.day}"


# --------------------------------------------------------------------------- surprises


def _surprises_card(sheet: DailySheet) -> Card | None:
    beat, missed = surprises(
        sheet.performances, SURPRISES_SHOWN, min_z=SURPRISE_Z, good_line=sheet.good_line
    )
    sat = expected_but_sat(sheet.performances)[:SURPRISES_SHOWN]
    if not (beat or missed or sat):
        return None
    items = [f"Better: {row}" for row in _surprise_rows(sheet, beat)]
    items += [f"Worse: {row}" for row in _surprise_rows(sheet, missed)]
    items += [f"Sat out: {p.name}{_paren(_owner(sheet, p.owner))} (expected to play)" for p in sat]
    return Card(
        id="daily-surprises",
        kind=CardKind.RECAP,
        title=f"Against expectations: {len(beat)} better, {len(missed)} worse"
        + (f", {len(sat)} sat out" if sat else ""),
        body="",
        items=bullets(items),
        reason=(
            "Expected is the projection pulled before the games, blended with the player's own "
            f"games before last night; a surprise is at least {SURPRISE_Z:g} spread off it. "
            f"{_scoring_note(sheet)}"
        ),
        data={
            "better": [_entry(sheet, p) for p in beat],
            "worse": [_entry(sheet, p) for p in missed],
            "sat_out": [_entry(sheet, p) for p in sat],
        },
    )


def _surprise_rows(sheet: DailySheet, perfs: Iterable[Performance]) -> list[str]:
    return [
        f"{p.name} {_val(sheet, p.value)}, expected {_val(sheet, p.expected.mean)}"
        + _paren(_owner(sheet, p.owner))
        for p in perfs
        if p.expected is not None
    ]


BLOCKS: dict[str, Callable[[DailySheet], Card | None]] = {
    "matchup": _matchup_card,
    "my-team": _my_team_card,
    "standouts": _standouts_card,
    "pickups": _pickups_card,
    "surprises": _surprises_card,
}
