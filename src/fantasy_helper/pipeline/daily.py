"""`fh run daily`: the morning run, engine-only until M4 adds the brain.

1. Ingest: fresh league data plus every new snapshot (box scores included).
2. For every lock-in league: load the facts, decide lock or pass for each open starter, and log
   each call in the registry before his next tip (never edited; a rerun logs nothing new).
3. Build one daily ReportDocument per lock-in league from what was logged.

Each league runs in its own savepoint, so one league failing never stops the others.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.domain.league import League, LineupMode
from fantasy_helper.domain.ports import Clock, SystemClock
from fantasy_helper.domain.recommendations import Recommendation
from fantasy_helper.domain.reports import ReportDocument
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.domain.users import User
from fantasy_helper.engine.lock_in import decide
from fantasy_helper.facts.lock_in import StarterWeek, load_lock_in
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.pipeline.ingest import (
    PLATFORM_NAMES,
    IngestReport,
    phone_summary,
    run_ingest,
)
from fantasy_helper.pipeline.lock_calls import dedupe_key, lock_or_pass_call
from fantasy_helper.pipeline.reconcile import LeagueCheck, reconcile
from fantasy_helper.pipeline.retry import classify_exception, outcome
from fantasy_helper.registry.calls import log_call
from fantasy_helper.reports.daily import lock_in_report
from fantasy_helper.storage.raw_store import RawStore
from fantasy_helper.storage.repositories import league_data, leagues

log = get_logger(__name__)


@dataclass
class LeagueDaily:
    league: League
    report: ReportDocument | None = None
    calls: int = 0
    new_calls: int = 0
    failure: FailureKind | None = None


@dataclass
class DailyResult:
    ingest: IngestReport
    leagues: list[LeagueDaily] = field(default_factory=list)
    checks: list[LeagueCheck] = field(default_factory=list)  # box scores vs the platforms

    @property
    def reports(self) -> list[ReportDocument]:
        return [lg.report for lg in self.leagues if lg.report is not None]

    def retryable_failures(self) -> int:
        parts = [lg.failure for lg in self.ingest.leagues] + [lg.failure for lg in self.leagues]
        return sum(1 for f in parts if f is not None and f.retryable)


def run_daily(
    session: Session,
    client: httpx.Client,
    *,
    store: RawStore,
    user: User,
    key: bytes,
    clock: Clock | None = None,
    events: RunEvents | None = None,
) -> DailyResult:
    clock = clock or SystemClock()
    ingest = run_ingest(
        session, client, store=store, user=user, key=key, clock=clock, events=events
    )
    result = DailyResult(ingest=ingest)
    now = clock.now()
    try:  # a check that breaks must never stop the run (its savepoint rolls back alone)
        with session.begin_nested():
            result.checks = reconcile(session, ingest, now=now)
    except Exception as exc:
        log.error("reconcile_failed", error=type(exc).__name__)
    if events is not None:
        events.step("Box scores checked", check_summary(result.checks))
    failed_in_ingest = {lg.league.id: lg.failure for lg in ingest.leagues if lg.failure}
    for league in leagues.list_for_user(session, user.id):
        rules = league_data.latest_rules(session, league.id)
        if rules is None or rules[1].lineup_mode is not LineupMode.LOCK_IN:
            continue
        if league.id in failed_in_ingest:
            result.leagues.append(LeagueDaily(league, failure=failed_in_ingest[league.id]))
            continue
        try:
            with session.begin_nested():
                result.leagues.append(_lock_in_league(session, league, user, now))
        except Exception as exc:
            kind = classify_exception(exc)
            log.error("lock_in_league_failed", platform=league.platform.value, kind=kind.value)
            result.leagues.append(LeagueDaily(league, failure=kind))
    if events is not None:
        done = [lg for lg in result.leagues if lg.failure is None]
        events.step(
            "Lock-or-pass calls built",
            f"{sum(lg.calls for lg in done)} calls in {len(done)} of {len(result.leagues)} "
            f"lock-in leagues ({sum(lg.new_calls for lg in done)} newly logged).",
        )
    return result


def open_decisions(starters: Sequence[StarterWeek]) -> list[StarterWeek]:
    """Starters with a game they can still lock: played, his next game not yet tipped, games
    still to come. The app can't see locks yet, so a player already locked still gets a card:
    a missed decision costs points, a card for a locked player costs a glance."""
    return [
        s for s in starters
        if not s.window_closed
        and s.latest is not None and s.latest.played and s.upcoming
    ]  # fmt: skip


def _lock_in_league(session: Session, league: League, user: User, now: datetime) -> LeagueDaily:
    sheet = load_lock_in(session, league, now=now)
    calls: list[tuple[StarterWeek, Recommendation]] = []
    new = 0
    for starter in open_decisions(sheet.starters):
        assert starter.latest is not None  # noqa: S101 (open_decisions guarantees it)
        decision = decide(
            starter.latest.points,
            [g.distribution for g in starter.upcoming],
            seed=starter.player_id.int % 2**31,  # repeatable per player
        )
        if decision is None:
            continue
        rec = lock_or_pass_call(sheet, starter, decision, user_id=user.id, now=now)
        logged, created = log_call(session, rec, dedupe_key(starter), now=now)
        calls.append((starter, logged))
        new += int(created)
    return LeagueDaily(
        league,
        report=lock_in_report(sheet, calls, user_id=user.id, generated_at=now),
        calls=len(calls),
        new_calls=new,
    )


def check_summary(checks: list[LeagueCheck]) -> str:
    """Counts only, by platform: "ESPN season totals 155/155; Sleeper one game a week 405/417"."""
    parts = [
        f"{PLATFORM_NAMES[c.league.platform]} {c.method} {c.agreement.matched}/{c.agreement.total}"
        for c in checks
        if c.agreement.total
    ]
    return "; ".join(parts) + "." if parts else "Nothing settled to compare yet."


def daily_summary(result: DailyResult, attempts: int) -> tuple[str, list[str]]:
    """What the phone hears: counts and kinds only, never names (ADR 0011)."""
    ingest_detail, problems = phone_summary(result.ingest, attempts)
    done = [lg for lg in result.leagues if lg.failure is None]
    detail = (
        f"Lock-in: {sum(lg.calls for lg in done)} calls in {len(done)} of {len(result.leagues)} "
        f"leagues. {ingest_detail}"
    )
    failed = Counter(
        (PLATFORM_NAMES[lg.league.platform], lg.failure)
        for lg in result.leagues
        if lg.failure is not None
        and lg.league.id not in {i.league.id for i in result.ingest.leagues if i.failure}
    )
    for (platform, kind), n in sorted(failed.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
        problems.append(
            f"lock-in calls failed for {n} {platform} league{'s' if n > 1 else ''}: "
            f"{outcome(kind, attempts)}"
        )
    for check in result.checks:
        if not check.ok:
            a = check.agreement
            problems.append(
                f"box scores agree with {PLATFORM_NAMES[check.league.platform]} {check.method} "
                f"for only {a.matched}/{a.total} (expected {check.minimum:.0%}+): check the "
                "box scores and scoring (ADR 0022)"
            )
    return detail, problems
