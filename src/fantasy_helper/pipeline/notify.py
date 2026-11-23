"""Telling the phone a report is ready (ADR 0030).

The report jobs call this after a report is committed, never inside the run's transaction: a push
announces a report that exists, not one that could still roll back. It sends once per user, kind
and date (`reports.notified_at`), tells nobody on a rerun, deletes a device the push service says
is gone, and **never raises**: whatever goes wrong comes back as a problem for the run's warning,
and the report is built either way.

Network calls happen between two short transactions, never inside one.
"""

from collections import Counter
from collections.abc import Callable, Collection, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from fantasy_helper.delivery.push import (
    PushNotConfiguredError,
    PushResult,
    PushSender,
    PushStatus,
    report_ready,
)
from fantasy_helper.domain.push import PushDevice, PushMessage
from fantasy_helper.domain.reports import ReportKind
from fantasy_helper.domain.users import User
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.storage.repositories import push_subscriptions, users
from fantasy_helper.storage.repositories import reports as saved_reports

log = get_logger(__name__)

# The weekly overview is rebuilt every morning (ADR 0026), and two notifications a minute apart
# every day would be noise: it notifies on this weekday, Monday, and updates quietly on the others.
# 0 is Monday (`date.weekday()`). Adam's to change (ADR 0030): one number.
WEEKLY_NOTIFY_WEEKDAY = 0

OpenSession = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class NotifyOutcome:
    devices: int = 0
    delivered: int = 0
    removed: int = 0  # devices the push service said are gone, now deleted
    skipped: str | None = None  # why nothing was sent, when that is by design (not a problem)
    problems: tuple[str, ...] = field(default=())  # for the run's warning

    def detail(self) -> str:
        """For the quiet ops step: counts only."""
        if self.skipped:
            return f"Nothing sent: {self.skipped}."
        bits = [f"{self.delivered} of {self.devices} devices notified"]
        if self.removed:
            bits.append(f"{self.removed} lost device(s) removed")
        return ", ".join(bits) + "."


def deliver(
    sender: PushSender, devices: Sequence[PushDevice], message: PushMessage
) -> list[tuple[PushDevice, PushResult]]:
    """Send one message to every device. A sender that raises is a failed send, not a crash."""
    out: list[tuple[PushDevice, PushResult]] = []
    for device in devices:
        try:
            result = sender.send(device, message)
        except Exception as exc:
            result = PushResult(PushStatus.FAILED, "unknown", reason=type(exc).__name__)
        log.info("push_sent", result=result.describe())
        out.append((device, result))
    return out


def record(
    session: Session, user: User, results: Sequence[tuple[PushDevice, PushResult]], now: datetime
) -> int:
    """Write down what the push services said: lost devices are deleted, accepted ones stamped.
    Returns how many devices were removed."""
    gone = [d.id for d, r in results if r.status is PushStatus.GONE]
    delivered = [d.id for d, r in results if r.status is PushStatus.DELIVERED]
    push_subscriptions.mark_success(session, user.id, delivered, now)
    return push_subscriptions.delete_by_ids(session, user.id, gone)


def problems_in(results: Sequence[tuple[PushDevice, PushResult]]) -> list[str]:
    """What went wrong, in words for the run's warning: which push service, which HTTP status and
    reason, and what it probably means. Never an endpoint, a key or a card's text."""
    out: list[str] = []
    refused = [r for _d, r in results if r.status is PushStatus.REFUSED]
    failed = [r for _d, r in results if r.status is PushStatus.FAILED]
    if refused:
        seen = Counter(r.describe() for r in refused)
        shown = "; ".join(f"{what} (x{n})" if n > 1 else what for what, n in seen.items())
        out.append(
            f"Report built, but the push service refused the push ({shown}): the VAPID key or the "
            "app's address in FH_APP_ORIGINS is probably wrong (docs/runbooks/phone-app.md, "
            "Part 3)."
        )
    if failed:
        seen = Counter(r.describe() for r in failed)
        shown = "; ".join(f"{what} (x{n})" if n > 1 else what for what, n in seen.items())
        out.append(
            f"Report built, but the push failed for {len(failed)} device(s) ({shown}). A rerun "
            "of the run tries again."
        )
    return out


def notify_report_ready(
    open_session: OpenSession,
    make_sender: Callable[[], PushSender],
    *,
    user_id: UUID,
    kind: ReportKind,
    dates: Collection[date],
    now: datetime,
) -> NotifyOutcome:
    """Announce the reports of this kind and these dates, if they haven't been, to every device
    the user has. The sender is made only when there is something to send, so a laptop with no
    push settings, and a user with no devices, are both quiet."""
    try:
        return _notify(open_session, make_sender, user_id=user_id, kind=kind, dates=dates, now=now)
    except Exception as exc:  # the report is built either way (ADR 0030)
        log.error("push_failed", kind=kind.value, error=type(exc).__name__)
        return NotifyOutcome(
            problems=(
                f"Report built, but the push failed unexpectedly ({type(exc).__name__}); see the "
                "run's log.",
            )
        )


def _notify(
    open_session: OpenSession,
    make_sender: Callable[[], PushSender],
    *,
    user_id: UUID,
    kind: ReportKind,
    dates: Collection[date],
    now: datetime,
) -> NotifyOutcome:
    if not dates:
        return NotifyOutcome(skipped="no report was saved")
    with open_session() as session:
        user = users.get_by_id(session, user_id)
        announced = saved_reports.announcement(session, user_id, kind, dates)
        devices = push_subscriptions.devices_for_user(session, user_id)
    if user is None or announced.reports == 0:
        return NotifyOutcome(skipped="no report was saved")
    if kind is ReportKind.WEEKLY:
        today = now.astimezone(ZoneInfo(user.timezone)).date()
        if today.weekday() != WEEKLY_NOTIFY_WEEKDAY:
            return NotifyOutcome(skipped="the weekly overview notifies on its one weekday only")
    if announced.already_sent:
        return NotifyOutcome(skipped="already sent for these reports")
    if not devices:
        return NotifyOutcome(skipped="no devices have notifications on")
    try:
        sender = make_sender()
    except PushNotConfiguredError as exc:
        return NotifyOutcome(
            devices=len(devices), problems=(f"Report built, but no push was sent: {exc}.",)
        )

    message = report_ready(kind, max(dates), reports=announced.reports, cards=announced.cards)
    results = deliver(sender, devices, message)
    delivered = sum(1 for _d, r in results if r.status is PushStatus.DELIVERED)
    with open_session() as session:
        removed = record(session, user, results, now)
        if delivered:
            saved_reports.mark_notified(session, user.id, kind, dates, now)
    return NotifyOutcome(
        devices=len(devices),
        delivered=delivered,
        removed=removed,
        problems=tuple(problems_in(results)),
    )
