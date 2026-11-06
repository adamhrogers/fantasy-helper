import json

from fantasy_helper.adapters.redaction import (
    FIXTURE_ESPN_LEAGUE_ID,
    FIXTURE_SLEEPER_LEAGUE_ID,
    Redactor,
    redact_espn,
    redact_sleeper,
)

SWID_A = "{8A1B2C3D-0000-4000-8000-ABCDEF012345}"
SWID_B = "{11111111-2222-4333-8444-555555555555}"

ESPN = {
    "id": 1345,
    "settings": {"name": "Brandeis Judges Crew"},
    "members": [
        {"id": SWID_A, "displayName": "jdoe_hoops", "firstName": "Jane", "lastName": "Doelittle",
         "notificationSettings": [{"id": "x"}]},
        {"id": SWID_B, "displayName": "bigsmith", "firstName": "Robert", "lastName": "Smithson"},
    ],
    "teams": [
        {
            "id": 3, "name": "Splash Mountain", "abbrev": "SPLM",
            "owners": [SWID_A], "primaryOwner": SWID_A,
            "roster": {"entries": [{
                "playerId": 4065648,
                "playerPoolEntry": {"player": {"fullName": "Victor Wembanyama"}},
            }]},
        },
    ],
    "transactions": [{"memberId": SWID_B, "items": [{"playerId": 4065648, "type": "ADD"}]}],
}  # fmt: skip


def test_espn_people_teams_and_league_are_replaced() -> None:
    r = Redactor()
    out = redact_espn(ESPN, r, league_id="1345")
    text = json.dumps(out)
    for original in ("Brandeis Judges Crew", "jdoe_hoops", "Doelittle", "Splash Mountain", "SPLM",
                     SWID_A, SWID_B, "8A1B2C3D"):  # fmt: skip
        assert original not in text
    assert out["id"] == FIXTURE_ESPN_LEAGUE_ID
    assert out["teams"][0]["name"] == "Team 3"
    assert "notificationSettings" not in out["members"][0]
    assert r.leaks(text, extra={"1345"}) == []


def test_public_player_data_is_kept() -> None:
    out = redact_espn(ESPN, Redactor(), league_id="1345")
    entry = out["teams"][0]["roster"]["entries"][0]
    assert entry["playerPoolEntry"]["player"]["fullName"] == "Victor Wembanyama"
    assert entry["playerId"] == 4065648


def test_member_ids_map_consistently_across_fields_and_files() -> None:
    r = Redactor()
    settings = redact_espn(ESPN, r, league_id="1345")
    moves = redact_espn({"transactions": [{"memberId": SWID_A}]}, r, league_id="1345")
    owner = settings["teams"][0]["owners"][0]
    assert owner == settings["members"][0]["id"] == settings["teams"][0]["primaryOwner"]
    assert moves["transactions"][0]["memberId"] == owner
    assert settings["transactions"][0]["memberId"] != owner


def test_the_leak_check_catches_a_name_in_an_unexpected_field() -> None:
    r = Redactor()
    data = dict(ESPN, notes={"trash_talk": "Splash Mountain is going down"})
    text = json.dumps(redact_espn(data, r, league_id="1345"))
    assert r.leaks(text) == ["team name"]


def test_the_leak_check_catches_credentials_and_whole_league_ids_only() -> None:
    r = Redactor()
    assert r.leaks('{"cookie": "s2-secret"}', extra={"s2-secret"}) == ["credential or league id"]
    assert r.leaks('{"playerId": 913450}', extra={"1345"}) == []  # not inside a longer number
    assert r.leaks('{"leagueId": 1345}', extra={"1345"}) == ["credential or league id"]


def test_sleeper_members_are_replaced_and_ids_stay_consistent() -> None:
    r = Redactor()
    users = redact_sleeper(
        "users",
        [{"user_id": "731122334455667788", "display_name": "LebronFan23",
          "metadata": {"team_name": "Lebronnn Lebronnnn"}, "avatar": "abc"}],
        r,
    )  # fmt: skip
    rosters = redact_sleeper(
        "rosters", [{"roster_id": 1, "owner_id": "731122334455667788", "players": ["4866"]}], r
    )
    league = redact_sleeper(
        "league",
        {
            "league_id": "1416782670947500032",
            "name": "Hoop League",
            "last_message_text_map": {"x": 1},
        },
        r,
    )
    text = json.dumps([users, rosters, league])
    assert users[0]["user_id"] == rosters[0]["owner_id"]
    assert users[0]["metadata"] == {"team_name": "Team 1"}
    assert league["league_id"] == FIXTURE_SLEEPER_LEAGUE_ID
    assert league["last_message_text_map"] is None
    assert rosters[0]["players"] == ["4866"]
    assert r.leaks(text) == []
    for original in ("LebronFan23", "Lebronnn", "731122334455667788", "Hoop League"):
        assert original not in text


def test_short_values_must_stand_alone_to_count() -> None:
    r = Redactor()
    r.track("team name", "AUTO")
    assert r.leaks('{"settings": {"isAutoReactivate": false}}') == []  # the 2026-11-06 false alarm
    assert r.leaks('{"abbrev": "AUTO"}') == ["team name"]
    assert r.leaks('{"note": "auto pilot"}') == ["team name"]


def test_a_members_first_name_matching_an_nba_player_is_not_a_leak() -> None:
    r = Redactor()
    data = {
        "members": [{
            "id": SWID_A, "displayName": "jdoe_hoops",
            "firstName": "Victor", "lastName": "Doelittle",
        }],
        "teams": [{"id": 1, "roster": {"entries": [{"playerPoolEntry": {"player": {
            "firstName": "Victor", "fullName": "Victor Wembanyama",
        }}}]}}],
    }  # fmt: skip
    text = json.dumps(redact_espn(data, r, league_id="1345"))
    assert r.leaks(text) == []
    assert "Victor Wembanyama" in text
    assert r.leaks('{"note": "Victor Doelittle"}') == ["member name"]  # the full name still counts
