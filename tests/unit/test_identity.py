import uuid
from datetime import date

import pytest

from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.records import PlayerKey, SourcePlayer
from fantasy_helper.identity.matching import Candidate, Matcher
from fantasy_helper.identity.names import normalize_name


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Moussa Diabaté", "moussa diabate"),
        ("P.J. Washington Jr.", "pj washington"),
        ("PJ Washington", "pj washington"),
        ("D'Angelo Russell", "dangelo russell"),
        ("D\u2019Angelo Russell", "dangelo russell"),  # curly apostrophe
        ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
        ("Gary Trent Jr", "gary trent"),
        ("Jaime Jaquez Jr.", "jaime jaquez"),
        ("Nikola Jokić", "nikola jokic"),
    ],
)
def test_normalize_name(raw: str, normalized: str) -> None:
    assert normalize_name(raw) == normalized


def sleeper(pid: str, name: str, team: str | None) -> SourcePlayer:
    return SourcePlayer(
        key=PlayerKey(source=IdSource.SLEEPER, external_id=pid), full_name=name, nba_team=team
    )


A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CANDIDATES = [
    Candidate(A, "moussa diabate", "CHA"),
    Candidate(B, "jalen williams", "OKC"),
    Candidate(C, "jalen williams", "DEN"),  # two NBA players share this name
]


def matcher(players: list[SourcePlayer], overrides: dict[str, uuid.UUID] | None = None) -> Matcher:
    return Matcher(CANDIDATES, players, overrides or {}, claimed=set())


def test_same_name_and_team_links() -> None:
    p = sleeper("2474", "Moussa Diabaté", "CHA")
    assert matcher([p]).match(p) == (A, "name_team")


def test_shared_names_link_only_with_the_team() -> None:
    okc = sleeper("1", "Jalen Williams", "OKC")
    nowhere = sleeper("2", "Jalen Williams", None)
    m = matcher([okc, nowhere])
    assert m.match(okc) == (B, "name_team")
    assert m.match(nowhere) is None  # ambiguous: never guessed


def test_a_unique_name_links_across_a_team_change() -> None:
    traded = sleeper("2474", "Moussa Diabate", "DAL")
    assert matcher([traded]).match(traded) == (A, "name_unique")


def test_overrides_win() -> None:
    nickname = sleeper("99", "Nic Claxton", "BKN")
    assert matcher([nickname], {"99": C}).match(nickname) == (C, "override")


def test_a_claimed_player_is_not_linked_twice() -> None:
    first, duplicate = (
        sleeper("2474", "Moussa Diabaté", "CHA"),
        sleeper("7", "Moussa Diabate", "CHA"),
    )
    m = Matcher(CANDIDATES, [first], {}, claimed={A})
    assert m.match(duplicate) is None


# ------------------------------------------------------------ birth dates: trade-proof links

J1, J2 = uuid.uuid4(), uuid.uuid4()
SHARED_NAME = [
    Candidate(J1, "jalen williams", "OKC", date(2001, 4, 14)),
    Candidate(J2, "jalen williams", "DEN", date(1999, 7, 19)),
]


def born(pid: str, name: str, team: str | None, birth: date | None) -> SourcePlayer:
    return SourcePlayer(
        key=PlayerKey(source=IdSource.SLEEPER, external_id=pid),
        full_name=name,
        nba_team=team,
        birth_date=birth,
    )


def test_a_traded_player_with_a_common_name_links_by_birth_date() -> None:
    # Sleeper already shows the new team; ESPN hasn't caught up. Name + team can't decide,
    # and the name isn't unique, but the birth date does.
    traded = born("1", "Jalen Williams", "MIA", date(2001, 4, 14))
    m = Matcher(SHARED_NAME, [traded], {}, claimed=set())
    assert m.match(traded) == (J1, "name_birthdate")


def test_birth_dates_a_day_apart_still_match() -> None:
    off_by_one = born("1", "Jalen Williams", None, date(2001, 4, 15))  # time-zone artifact
    assert Matcher(SHARED_NAME, [off_by_one], {}, claimed=set()).match(off_by_one) == (
        J1,
        "name_birthdate",
    )


def test_a_wrong_birth_date_does_not_veto_an_unambiguous_match() -> None:
    # One platform's data is wrong (Zach Edey's birth month differs by 2 on 2026-11-06).
    edey = Candidate(A, "zach edey", "MEM", date(2002, 3, 14))
    sleeper_edey = born("9", "Zach Edey", "MEM", date(2002, 5, 14))
    assert Matcher([edey], [sleeper_edey], {}, claimed=set()).match(sleeper_edey) == (
        A,
        "name_team",
    )


def test_birth_date_beats_a_stale_team() -> None:
    p = born("1", "Jalen Williams", "OKC", date(1999, 7, 19))  # the Denver Jalen, listed under OKC
    assert Matcher(SHARED_NAME, [p], {}, claimed=set()).match(p) == (J2, "name_birthdate")
