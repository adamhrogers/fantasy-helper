"""What the phone hears about a daily run: counts and kinds only (ADR 0011), never names."""

from datetime import UTC, date, datetime
from uuid import uuid4

from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.engine.reconcile import Agreement
from fantasy_helper.pipeline.daily import DailyResult, LeagueDaily, daily_summary, recap_summary
from fantasy_helper.pipeline.ingest import IngestReport, LeagueResult


def league(platform: Platform, name: str) -> League:
    return League(
        user_id=uuid4(), platform=platform, external_id=str(uuid4().int)[:8], season=2027,
        name=name,
    )  # fmt: skip


def built(platform: Platform, name: str, cards: int, **kwargs: object) -> LeagueDaily:
    lg = league(platform, name)
    report = ReportDocument(
        user_id=lg.user_id, league_id=lg.id, kind=ReportKind.DAILY, for_date=date(2026, 11, 5),
        generated_at=datetime(2026, 11, 5, 11, 20, tzinfo=UTC),
        cards=tuple(
            Card(id=f"c{i}", kind=CardKind.RECAP, title="t", body="b") for i in range(cards)
        ),
        engine_only=True,
    )  # fmt: skip
    return LeagueDaily(lg, report=report, recap_cards=cards, **kwargs)  # type: ignore[arg-type]


def test_a_clean_run_reports_counts_for_every_league() -> None:
    result = DailyResult(IngestReport())
    result.leagues = [
        built(Platform.SLEEPER, "Secret Name", 4, lock_in=True, calls=3, new_calls=3),
        built(Platform.ESPN, "Another Name", 5, day_check=Agreement(4, 4)),
        built(Platform.ESPN, "Third Name", 2, day_check=Agreement(10, 10)),
    ]
    detail, problems = daily_summary(result, attempts=1)
    assert detail.startswith(
        "Lock-in: 3 calls in 1 of 1 leagues. Recap: 11 cards in 3 of 3 leagues."
    )
    assert "Last night's totals match the platforms' own: 14/14." in detail
    assert problems == []
    assert not any(lg.league.name in detail for lg in result.leagues)


def test_nothing_checked_says_nothing_about_totals() -> None:
    result = DailyResult(IngestReport())
    result.leagues = [built(Platform.ESPN, "Only", 1)]
    assert recap_summary(result.leagues) == "1 cards in 1 of 1 leagues."


def test_totals_that_disagree_with_the_platform_are_flagged_by_count_and_size() -> None:
    result = DailyResult(IngestReport())
    result.leagues = [
        built(Platform.ESPN, "A", 3, day_check=Agreement(2, 4, (1.5, -3.25))),
        built(Platform.ESPN, "B", 3, day_check=Agreement(4, 4)),
        built(Platform.ESPN, "C", 3, day_check=Agreement(3, 4, (0.75,))),
    ]
    _, problems = daily_summary(result, attempts=1)
    assert problems == [
        "last night's totals differ from the platform's own in 2 leagues (largest 3.2): check the "
        "stored lineups and box scores (ADR 0022)"
    ]


def test_a_recap_that_failed_beside_saved_lock_calls_is_a_warning_with_its_own_wording() -> None:
    lock = built(Platform.SLEEPER, "L", 0, lock_in=True, calls=2, recap_failure=FailureKind.NETWORK)
    result = DailyResult(IngestReport(), [lock])
    assert result.retryable_failures() == 1  # a recap a retry could fix is retried
    _, problems = daily_summary(result, attempts=3)
    assert problems == [
        "recap cards failed for 1 Sleeper league (the lock calls are saved): couldn't connect, "
        "after 3 attempts"
    ]
    lock.recap_failure = FailureKind.BUG
    assert result.retryable_failures() == 0  # a bug is not


def test_a_league_that_failed_outright_says_which_part_and_never_its_name() -> None:
    recap_only = LeagueDaily(league(Platform.ESPN, "Hidden"), failure=FailureKind.BUG)
    locks = LeagueDaily(
        league(Platform.SLEEPER, "Also Hidden"), lock_in=True, failure=FailureKind.SERVER_ERROR
    )
    result = DailyResult(IngestReport(), [recap_only, locks])
    detail, problems = daily_summary(result, attempts=3)
    assert problems == [
        "the daily recap failed for 1 ESPN league: a bug in our code; not retried (retrying can't "
        "fix it)",
        "lock-in calls failed for 1 Sleeper league: the source's servers erred, after 3 attempts",
    ]
    assert "Hidden" not in detail + " ".join(problems)
    assert result.retryable_failures() == 1  # only the server error can clear up


def test_a_league_that_failed_in_ingest_is_not_reported_twice() -> None:
    broken = league(Platform.ESPN, "Down")
    ingest = IngestReport(leagues=[LeagueResult(league=broken, failure=FailureKind.AUTH)])
    result = DailyResult(ingest, [LeagueDaily(broken, failure=FailureKind.AUTH)])
    _, problems = daily_summary(result, attempts=1)
    assert len([p for p in problems if "failed" in p]) == 1  # ingest's line, not another here


def test_a_skipped_recap_is_a_counted_warning_that_no_retry_is_asked_for() -> None:
    result = DailyResult(IngestReport())
    skipped = LeagueDaily(league(Platform.ESPN, "Hidden"), recap_skipped=True)
    result.leagues = [
        skipped,
        built(Platform.ESPN, "Fine", 3),
        LeagueDaily(league(Platform.ESPN, "B"), recap_skipped=True),
    ]
    detail, problems = daily_summary(result, attempts=3)
    assert problems == [
        "the daily recap was skipped for 2 leagues: settings, the schedule or your team aren't "
        "loaded yet (nothing to retry: check `fh league add` and that the schedule is loaded)"
    ]
    assert result.retryable_failures() == 0
    assert "Hidden" not in detail + " ".join(problems)
