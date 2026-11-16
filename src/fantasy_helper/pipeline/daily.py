"""`fh run daily`: the morning run, engine-only until M4 adds the brain.

1. Ingest: fresh league data plus every new snapshot (box scores included).
2. For every lock-in league: load the facts, decide lock or pass for each open starter, and log
   each call in the registry before his next tip (never edited; a rerun logs nothing new).
3. For every league, of any format: the "what happened" cards for last night in that league's own
   scoring (facts.daily, reports.recap): the matchup, his team, the league's standouts, pickups
   and what was expected against what wasn't. Our day totals are checked against the platform's.
4. One daily ReportDocument per league: lock cards first, then the recap. Saved (a rerun replaces
   the same day's report, ADR 0026).

Each league runs in its own savepoints, and the lock calls and the recap in separate ones: a
recap that breaks never costs the lock calls (which have deadlines), and the other way round.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.league import League, LineupMode
from fantasy_helper.domain.ports import Clock, SystemClock
from fantasy_helper.domain.recommendations import Recommendation
from fantasy_helper.domain.reports import Card, ReportDocument
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.domain.users import User
from fantasy_helper.engine.lock_in import decide
from fantasy_helper.engine.reconcile import Agreement
from fantasy_helper.facts.daily import load_daily
from fantasy_helper.facts.lock_in import MissingFactsError, StarterWeek, load_lock_in
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
from fantasy_helper.reports.daily import daily_report, lock_in_cards, lock_in_unavailable
from fantasy_helper.reports.recap import recap_cards
from fantasy_helper.storage.raw_store import RawStore
from fantasy_helper.storage.repositories import league_data, leagues, nba
from fantasy_helper.storage.repositories import reports as saved_reports
from fantasy_helper.storage.tables import PlayerGameStatRow

log = get_logger(__name__)


@dataclass
class LeagueDaily:
    league: League
    report: ReportDocument | None = None
    lock_in: bool = False
    calls: int = 0
    new_calls: int = 0
    # The league's report (or its lock calls) couldn't be built: the run counts it as failed.
    failure: FailureKind | None = None
    # The recap cards couldn't be built but the report was still saved with its lock cards:
    # a warning, and a retry if a retry can fix it.
    recap_failure: FailureKind | None = None
    # The recap was skipped because the league's own facts aren't there (settings, the schedule or
    # a team marked as his). That is a gap in the data, not a failure: nothing broke, a retry
    # can't fill it, so it is a warning with what to do, never a failed or retried run.
    recap_skipped: bool = False
    recap_cards: int = 0
    # Our totals for last night and the matchup against the platform's own, both teams.
    day_check: Agreement = field(default_factory=lambda: Agreement(0, 0))


@dataclass
class DailyResult:
    ingest: IngestReport
    leagues: list[LeagueDaily] = field(default_factory=list)
    checks: list[LeagueCheck] = field(default_factory=list)  # box scores vs the platforms

    @property
    def reports(self) -> list[ReportDocument]:
        return [lg.report for lg in self.leagues if lg.report is not None]

    def retryable_failures(self) -> int:
        parts = (
            [lg.failure for lg in self.ingest.leagues]
            + [lg.failure for lg in self.leagues]
            + [lg.recap_failure for lg in self.leagues]
        )
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
    box_scores: dict[int, list[PlayerGameStatRow]] = {}  # one load per season for the whole run
    try:  # a check that breaks must never stop the run (its savepoint rolls back alone)
        with session.begin_nested():
            result.checks = reconcile(session, ingest, now=now, box_scores=box_scores)
    except Exception as exc:
        log.error("reconcile_failed", error=type(exc).__name__)
    if events is not None:
        events.step("Box scores checked", check_summary(result.checks))
    failed_in_ingest = {lg.league.id: lg.failure for lg in ingest.leagues if lg.failure}
    for league in leagues.list_for_user(session, user.id):
        rules = league_data.latest_rules(session, league.id)
        lock_in = rules is not None and rules[1].lineup_mode is LineupMode.LOCK_IN
        if league.id in failed_in_ingest:
            result.leagues.append(
                LeagueDaily(league, lock_in=lock_in, failure=failed_in_ingest[league.id])
            )
            continue
        if rules is None:  # never loaded: ingest would have reported it
            continue
        if league.season not in box_scores:
            box_scores[league.season] = nba.box_scores(session, league.season)
        result.leagues.append(
            _league_daily(session, league, user, now, box_scores[league.season], lock_in=lock_in)
        )
    if events is not None:
        locked = [lg for lg in result.leagues if lg.lock_in and lg.failure is None]
        if any(lg.lock_in for lg in result.leagues):
            events.step(
                "Lock-or-pass calls built",
                f"{sum(lg.calls for lg in locked)} calls in {len(locked)} of "
                f"{sum(lg.lock_in for lg in result.leagues)} lock-in leagues "
                f"({sum(lg.new_calls for lg in locked)} newly logged).",
            )
        events.step("Recap built", recap_summary(result.leagues))
    return result


def _league_daily(
    session: Session,
    league: League,
    user: User,
    now: datetime,
    box_scores: Sequence[PlayerGameStatRow],
    *,
    lock_in: bool,
) -> LeagueDaily:
    """One league's daily report: lock-or-pass cards (lock-in leagues), then what happened."""
    out = LeagueDaily(league, lock_in=lock_in)
    cards: list[Card] = []
    if lock_in:
        try:
            with session.begin_nested():
                lock_cards, out.calls, out.new_calls = _lock_in_cards(
                    session, league, user, now, box_scores
                )
            cards += lock_cards
        except Exception as exc:
            out.failure = classify_exception(exc)
            log.error(
                "lock_in_league_failed", platform=league.platform.value, kind=out.failure.value
            )
            cards.append(lock_in_unavailable())
    sheet = None
    try:
        with session.begin_nested():
            sheet = load_daily(session, league, now=now, box_scores=box_scores)
    except MissingFactsError:
        out.recap_skipped = True
        log.warning("recap_skipped", platform=league.platform.value, reason="facts_missing")
    except Exception as exc:
        _recap_broke(out, league, classify_exception(exc), lock_in)
    if sheet is not None:
        # Building the cards is our own code on facts already loaded, so a failure here is a bug
        # whatever the exception looks like (never "the source changed its format").
        try:
            recap = recap_cards(sheet)
        except Exception:
            _recap_broke(out, league, FailureKind.BUG, lock_in)
        else:
            cards += recap
            out.recap_cards = len(recap)
            if sheet.matchup is not None:
                out.day_check = sheet.matchup.checked
    if not cards or (out.failure is not None and out.recap_cards == 0):
        return out  # nothing worth saving: no calls and no recap (it failed, or was skipped)
    try:
        with session.begin_nested():
            report = daily_report(
                league_id=league.id,
                user_id=user.id,
                for_date=now.astimezone(NBA_TIMEZONE).date(),
                generated_at=now,
                cards=cards,
            )
            # A rerun keeps the first saved id, so the report handed on (to the push, the run
            # summary) carries the id that's actually stored.
            out.report = report.model_copy(update={"id": saved_reports.save(session, report)})
    except Exception as exc:
        out.failure = out.failure or classify_exception(exc)
        log.error("daily_report_not_saved", platform=league.platform.value, kind=out.failure.value)
    return out


def _recap_broke(out: LeagueDaily, league: League, kind: FailureKind, lock_in: bool) -> None:
    """A recap that couldn't be built. Beside saved lock calls it is a warning (and a retry if a
    retry can help); as a league's whole report it fails the league."""
    log.error("recap_failed", platform=league.platform.value, kind=kind.value)
    if lock_in and out.failure is None:
        out.recap_failure = kind  # the lock cards still make a report
    elif out.failure is None:
        out.failure = kind  # the recap is this league's whole report


def open_decisions(starters: Sequence[StarterWeek]) -> list[StarterWeek]:
    """Starters with a game they can still lock: played, his next game not yet tipped, games
    still to come. The app can't see locks yet, so a player already locked still gets a card:
    a missed decision costs points, a card for a locked player costs a glance."""
    return [
        s for s in starters
        if not s.window_closed
        and s.latest is not None and s.latest.played and s.upcoming
    ]  # fmt: skip


def _lock_in_cards(
    session: Session,
    league: League,
    user: User,
    now: datetime,
    box_scores: Sequence[PlayerGameStatRow],
) -> tuple[list[Card], int, int]:
    """Decide and log each open lock-or-pass call; return the cards, calls, and new calls."""
    sheet = load_lock_in(session, league, now=now, box_scores=box_scores)
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
    return lock_in_cards(sheet, calls), len(calls), new


def check_summary(checks: list[LeagueCheck]) -> str:
    """Counts only, by platform: "ESPN season totals 155/155; Sleeper one game a week 405/417"."""
    parts = [
        f"{PLATFORM_NAMES[c.league.platform]} {c.method} {c.agreement.matched}/{c.agreement.total}"
        for c in checks
        if c.agreement.total
    ]
    return "; ".join(parts) + "." if parts else "Nothing settled to compare yet."


def recap_summary(leagues: Sequence[LeagueDaily]) -> str:
    """Counts only (ADR 0011): cards built, and how our day totals fared against the platforms'."""
    built = [lg for lg in leagues if lg.report is not None]
    text = (
        f"{sum(lg.recap_cards for lg in leagues)} cards in {len(built)} of {len(leagues)} leagues."
    )
    checked = sum((lg.day_check for lg in leagues), Agreement(0, 0))
    if checked.total:
        text += f" Last night's totals match the platforms' own: {checked.matched}/{checked.total}."
    return text


def daily_summary(result: DailyResult, attempts: int) -> tuple[str, list[str]]:
    """What the phone hears: counts and kinds only, never names (ADR 0011)."""
    ingest_detail, problems = phone_summary(result.ingest, attempts)
    lock = [lg for lg in result.leagues if lg.lock_in]
    parts = []
    if lock:
        done = [lg for lg in lock if lg.failure is None]
        parts.append(
            f"Lock-in: {sum(lg.calls for lg in done)} calls in {len(done)} of {len(lock)} leagues."
        )
    parts.append(f"Recap: {recap_summary(result.leagues)}")
    detail = " ".join(parts) + f" {ingest_detail}"
    in_ingest = {i.league.id for i in result.ingest.leagues if i.failure}
    failed = Counter(
        (PLATFORM_NAMES[lg.league.platform], lg.failure, lg.lock_in)
        for lg in result.leagues
        if lg.failure is not None and lg.league.id not in in_ingest
    )
    for (platform, kind, was_lock_in), n in sorted(
        failed.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
    ):
        what = "lock-in calls" if was_lock_in else "the daily recap"
        problems.append(
            f"{what} failed for {n} {platform} league{'s' if n > 1 else ''}: "
            f"{outcome(kind, attempts)}"
        )
    recap_failed = Counter(
        (PLATFORM_NAMES[lg.league.platform], lg.recap_failure)
        for lg in result.leagues
        if lg.recap_failure is not None
    )
    for (platform, kind), n in sorted(
        recap_failed.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
    ):
        problems.append(
            f"recap cards failed for {n} {platform} league{'s' if n > 1 else ''} (the lock calls "
            f"are saved): {outcome(kind, attempts)}"
        )
    skipped = sum(1 for lg in result.leagues if lg.recap_skipped)
    if skipped:
        problems.append(
            f"the daily recap was skipped for {skipped} league{'s' if skipped > 1 else ''}: "
            "settings, the schedule or your team aren't loaded yet (nothing to retry: check "
            "`fh league add` and that the schedule is loaded)"
        )
    off = [lg for lg in result.leagues if lg.day_check.matched < lg.day_check.total]
    if off:
        largest = max(abs(g) for lg in off for g in lg.day_check.gaps)
        problems.append(
            f"last night's totals differ from the platform's own in {len(off)} league"
            f"{'s' if len(off) > 1 else ''} (largest {largest:.1f}): check the stored lineups "
            "and box scores (ADR 0022)"
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
