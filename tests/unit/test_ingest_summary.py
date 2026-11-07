"""What the phone hears about an ingest: counts and kinds, never names, at any scale."""

from uuid import uuid4

from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.pipeline.ingest import IngestReport, LeagueResult, phone_summary

MB = 1024 * 1024


def league(platform: Platform, name: str) -> League:
    return League(
        user_id=uuid4(), platform=platform, external_id=str(uuid4().int)[:8], season=2027,
        name=name,
    )  # fmt: skip


def test_fifteen_leagues_read_as_counts() -> None:
    names = [f"Friend League {i}" for i in range(15)]
    report = IngestReport(game_log_rows=420, projection_rows=2000, database_bytes=150 * MB)
    for i, name in enumerate(names):
        platform = Platform.ESPN if i < 9 else Platform.SLEEPER
        failure = (
            FailureKind.AUTH if i == 0
            else FailureKind.SERVER_ERROR if i in (9, 10)
            else None
        )  # fmt: skip
        result = LeagueResult(league=league(platform, name), failure=failure)
        if i == 3:
            result.unresolved_players = 2
        report.leagues.append(result)

    detail, problems = phone_summary(report, attempts=3)
    assert detail.startswith("Leagues: 12 of 15 loaded.")
    assert "30% of the free plan" in detail
    assert problems == [
        "1 ESPN league failed: access denied (expired cookies? re-run fh league add); "
        "not retried (retrying can't fix it)",
        "2 Sleeper leagues failed: the source's servers erred, after 3 attempts",
        "2 rostered players unresolved in 1 leagues",
    ]
    text = detail + " ".join(problems)
    assert not any(name in text for name in names)
    assert not any(r.league.external_id in text for r in report.leagues)


def test_a_clean_run_has_nothing_to_flag() -> None:
    report = IngestReport(database_bytes=25 * MB)
    report.leagues.append(LeagueResult(league=league(Platform.ESPN, "Mine")))
    detail, problems = phone_summary(report, attempts=1)
    assert detail.startswith("Leagues: 1 of 1 loaded.")
    assert problems == []
