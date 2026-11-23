"""Telling the phone a report is ready (ADR 0030): when it is sent, when it stays quiet, and what a
failed push becomes. The database is replaced by recorders here; tests/integration/test_push.py
holds the same rules against Postgres."""

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from fantasy_helper.delivery.push import PushNotConfiguredError, PushResult, PushStatus
from fantasy_helper.domain.push import PushDevice, PushMessage
from fantasy_helper.domain.reports import ReportKind
from fantasy_helper.domain.users import Role, User
from fantasy_helper.pipeline import notify
from fantasy_helper.pipeline.notify import WEEKLY_NOTIFY_WEEKDAY, NotifyOutcome
from fantasy_helper.storage.repositories import push_subscriptions, users
from fantasy_helper.storage.repositories import reports as saved_reports
from fantasy_helper.storage.repositories.reports import Announcement

USER = User(email="a@example.com", display_name="A", timezone="America/New_York", role=Role.OWNER)
DAY = date(2026, 11, 20)  # a Friday
FRIDAY_MORNING = datetime(2026, 11, 20, 11, 20, tzinfo=UTC)  # 06:20 in New York
MONDAY_MORNING = datetime(2026, 11, 23, 11, 20, tzinfo=UTC)


def device(n: int = 1) -> PushDevice:
    return PushDevice(id=uuid4(), endpoint=f"https://push.example.test/{n}", p256dh="k", auth="a")


class FakeSender:
    def __init__(self, *statuses: PushStatus, raises: Exception | None = None) -> None:
        self.statuses = list(statuses) or [PushStatus.DELIVERED]
        self.raises = raises
        self.sent: list[tuple[PushDevice, PushMessage]] = []

    def send(self, d: PushDevice, message: PushMessage) -> PushResult:
        self.sent.append((d, message))
        if self.raises:
            raise self.raises
        status = self.statuses[min(len(self.sent), len(self.statuses)) - 1]
        http = {PushStatus.DELIVERED: None, PushStatus.GONE: 410, PushStatus.REFUSED: 403,
                PushStatus.FAILED: 503}[status]  # fmt: skip
        reason = "BadJwtToken" if status is PushStatus.REFUSED else None
        return PushResult(status, "push.example.test", http, reason)


class World:
    """The database as the notifier sees it, as recorders."""

    def __init__(
        self,
        devices: list[PushDevice],
        *,
        reports: int = 2,
        cards: int = 9,
        already_sent: bool = False,
        user: User | None = USER,
    ) -> None:
        self.devices = devices
        self.announcement = Announcement(reports=reports, cards=cards, already_sent=already_sent)
        self.user = user
        self.sessions = 0
        self.deleted: list[UUID] = []
        self.stamped: list[UUID] = []
        self.marked: list[tuple[ReportKind, set[date]]] = []

    @contextmanager
    def session(self) -> Iterator[Session]:
        self.sessions += 1
        yield cast(Session, object())


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    """A user with two devices and two saved reports, by default."""
    w = World([device(1), device(2)])
    monkeypatch.setattr(users, "get_by_id", lambda _s, _id: w.user)
    monkeypatch.setattr(saved_reports, "announcement", lambda _s, *_a: w.announcement)
    monkeypatch.setattr(push_subscriptions, "devices_for_user", lambda _s, _id: w.devices)

    def delete_by_ids(_s: object, _user: UUID, ids: Collection[UUID]) -> int:
        w.deleted.extend(ids)
        return len(ids)

    def mark_success(_s: object, _user: UUID, ids: Collection[UUID], _at: datetime) -> None:
        w.stamped.extend(ids)

    def mark_notified(
        _s: object, _user: UUID, kind: ReportKind, dates: Collection[date], _at: datetime
    ) -> int:
        w.marked.append((kind, set(dates)))
        return 1

    monkeypatch.setattr(push_subscriptions, "delete_by_ids", delete_by_ids)
    monkeypatch.setattr(push_subscriptions, "mark_success", mark_success)
    monkeypatch.setattr(saved_reports, "mark_notified", mark_notified)
    return w


def run(
    w: World,
    sender: FakeSender | None = None,
    *,
    kind: ReportKind = ReportKind.DAILY,
    now: datetime = FRIDAY_MORNING,
    make_sender: Any = None,
) -> NotifyOutcome:
    chosen = sender or FakeSender()
    return notify.notify_report_ready(
        w.session,
        make_sender or (lambda: chosen),
        user_id=USER.id,
        kind=kind,
        dates={DAY},
        now=now,
    )


def test_it_tells_every_device_once_and_marks_the_reports(world: World) -> None:
    sender = FakeSender()
    outcome = run(world, sender)
    assert (outcome.devices, outcome.delivered, outcome.removed) == (2, 2, 0)
    assert outcome.problems == ()
    assert outcome.skipped is None
    assert [m.title for _d, m in sender.sent] == ["Today's report is ready"] * 2
    assert sender.sent[0][1].body == "9 cards in 2 leagues."
    assert sender.sent[0][1].tag == "daily-2026-11-20"
    assert world.marked == [(ReportKind.DAILY, {DAY})]
    assert sorted(world.stamped) == sorted(d.id for d in world.devices)
    assert outcome.detail() == "2 of 2 devices notified."


def test_nothing_is_sent_when_it_already_went_out(world: World) -> None:
    world.announcement = Announcement(reports=2, cards=9, already_sent=True)
    sender = FakeSender()
    outcome = run(world, sender)
    assert sender.sent == []
    assert world.marked == []
    assert outcome.skipped == "already sent for these reports"
    assert outcome.problems == ()


def test_no_devices_means_no_push_no_warning_and_the_sender_is_never_even_made(
    world: World,
) -> None:
    world.devices = []

    def make() -> FakeSender:
        raise AssertionError("a laptop with no devices must not need push settings")

    outcome = run(world, make_sender=make)
    assert outcome.skipped == "no devices have notifications on"
    assert outcome.problems == ()
    assert outcome.delivered == 0
    assert world.marked == []


def test_no_saved_report_means_nothing_to_announce(world: World) -> None:
    world.announcement = Announcement(reports=0, cards=0, already_sent=False)
    sender = FakeSender()
    assert run(world, sender).skipped == "no report was saved"
    assert sender.sent == []
    world.user = None  # a user that vanished between the run and the push
    assert run(world, sender).skipped == "no report was saved"


def test_the_weekly_overview_notifies_on_monday_only_in_the_users_own_time(world: World) -> None:
    assert WEEKLY_NOTIFY_WEEKDAY == 0  # Monday: the one constant Adam can change (ADR 0030)
    sender = FakeSender()
    friday = run(world, sender, kind=ReportKind.WEEKLY, now=FRIDAY_MORNING)
    assert friday.skipped is not None
    assert sender.sent == []
    assert world.marked == []

    # 03:00 UTC on Monday is still Sunday evening in New York: not Monday yet for this user.
    sunday_in_new_york = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)
    assert run(world, sender, kind=ReportKind.WEEKLY, now=sunday_in_new_york).skipped is not None

    monday = run(world, sender, kind=ReportKind.WEEKLY, now=MONDAY_MORNING)
    assert monday.delivered == 2
    assert sender.sent[0][1].title == "This week's overview is ready"
    assert sender.sent[0][1].path == "/week"
    assert world.marked == [(ReportKind.WEEKLY, {DAY})]


def test_the_daily_report_notifies_every_day_of_the_week(world: World) -> None:
    for day in range(16, 23):  # Monday 16 to Sunday 22 November 2026
        sender = FakeSender()
        now = datetime(2026, 11, day, 11, 20, tzinfo=UTC)
        assert run(world, sender, now=now).delivered == 2, day


def test_a_device_the_push_service_says_is_gone_is_deleted_and_the_rest_still_count(
    world: World,
) -> None:
    sender = FakeSender(PushStatus.GONE, PushStatus.DELIVERED)
    outcome = run(world, sender)
    assert world.deleted == [world.devices[0].id]
    assert world.stamped == [world.devices[1].id]
    assert (outcome.delivered, outcome.removed) == (1, 1)
    assert outcome.problems == ()  # a lost device is housekeeping, not a warning
    assert world.marked  # the one that was notified counts
    assert outcome.detail() == "1 of 2 devices notified, 1 lost device(s) removed."


def test_when_every_device_is_gone_they_are_all_deleted_and_nothing_is_marked(
    world: World,
) -> None:
    outcome = run(world, FakeSender(PushStatus.GONE))
    assert sorted(world.deleted) == sorted(d.id for d in world.devices)
    assert outcome.delivered == 0
    assert world.marked == []


def test_a_refused_push_is_a_warning_that_says_why_and_leaves_the_reports_unmarked(
    world: World,
) -> None:
    outcome = run(world, FakeSender(PushStatus.REFUSED))
    assert outcome.delivered == 0  # a rerun may try again
    assert world.marked == []
    (problem,) = outcome.problems
    assert problem.startswith("Report built, but the push service refused the push")
    assert "push.example.test refused HTTP 403 BadJwtToken (x2)" in problem
    assert "FH_APP_ORIGINS" in problem
    assert "Part 3" in problem
    assert "https://push.example.test/1" not in problem  # never the endpoint


def test_a_push_service_that_is_down_is_a_warning_and_a_rerun_tries_again(world: World) -> None:
    outcome = run(world, FakeSender(PushStatus.FAILED))
    (problem,) = outcome.problems
    assert "push failed for 2 device(s)" in problem
    assert "HTTP 503" in problem
    assert world.marked == []


def test_one_device_failing_beside_one_that_worked_still_warns(world: World) -> None:
    outcome = run(world, FakeSender(PushStatus.DELIVERED, PushStatus.FAILED))
    assert outcome.delivered == 1  # announced to the one that got it
    assert world.marked
    assert len(outcome.problems) == 1
    assert "for 1 device(s)" in outcome.problems[0]


def test_missing_push_settings_are_a_warning_naming_the_setting_not_a_failure(
    world: World,
) -> None:
    def unset() -> FakeSender:
        raise PushNotConfiguredError(
            "FH_VAPID_PRIVATE_KEY isn't set (scripts/vapid-setup.sh makes it)"
        )

    outcome = run(world, make_sender=unset)
    assert outcome.delivered == 0
    assert world.marked == []
    assert outcome.problems == (
        "Report built, but no push was sent: FH_VAPID_PRIVATE_KEY isn't set "
        "(scripts/vapid-setup.sh makes it).",
    )


def test_it_never_raises_whatever_breaks(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The report is built either way: a sender that blows up, a database that is gone and a bug
    all come back as a warning."""
    outcome = run(world, FakeSender(raises=RuntimeError("boom")))
    assert outcome.delivered == 0
    assert "refused" not in outcome.problems[0]
    assert "push failed for 2 device(s)" in outcome.problems[0]

    def broken(*_a: object, **_k: object) -> Announcement:
        raise ConnectionError("database went away")

    monkeypatch.setattr(saved_reports, "announcement", broken)
    outcome = run(world)
    assert outcome.problems == (
        "Report built, but the push failed unexpectedly (ConnectionError); see the run's log.",
    )
    assert "database went away" not in outcome.problems[0]  # the kind, never the text


def test_the_network_is_never_called_inside_a_transaction(world: World) -> None:
    """Two short transactions, with the sends between them: a slow push service can't hold a
    database connection (or the run's lock) open."""
    open_now = 0
    seen_open_during_send: list[int] = []

    @contextmanager
    def session() -> Iterator[Session]:
        nonlocal open_now
        open_now += 1
        try:
            yield cast(Session, object())
        finally:
            open_now -= 1

    class Watching(FakeSender):
        def send(self, d: PushDevice, message: PushMessage) -> PushResult:
            seen_open_during_send.append(open_now)
            return super().send(d, message)

    sender = Watching()
    notify.notify_report_ready(
        session, lambda: sender, user_id=USER.id, kind=ReportKind.DAILY, dates={DAY},
        now=FRIDAY_MORNING,
    )  # fmt: skip
    assert seen_open_during_send == [0, 0]
