"""Push against Postgres (ADR 0030): a report is announced once per user, kind and date; a rerun
is quiet, even one that replaces the report; a device a push service says is gone is deleted; one
user's push never touches another's devices or reports."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from fantasy_helper.delivery.push import PushResult, PushStatus
from fantasy_helper.domain.push import PushDevice, PushMessage
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind
from fantasy_helper.domain.users import User
from fantasy_helper.pipeline.notify import NotifyOutcome, notify_report_ready
from fantasy_helper.storage.repositories import leagues, push_subscriptions
from fantasy_helper.storage.repositories import reports as saved_reports
from fantasy_helper.storage.tables import PushSubscriptionRow, ReportRow
from tests.integration.test_ingest import setup_owner

pytestmark = pytest.mark.db

DAY = date(2026, 11, 20)  # a Friday
FRIDAY = datetime(2026, 11, 20, 11, 20, tzinfo=UTC)
MONDAY = datetime(2026, 11, 23, 11, 20, tzinfo=UTC)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


class Sender:
    """A push service that answers each endpoint as told (default: accepts)."""

    def __init__(self, answers: dict[str, PushStatus] | None = None) -> None:
        self.answers = answers or {}
        self.sent: list[tuple[str, PushMessage]] = []

    def send(self, device: PushDevice, message: PushMessage) -> PushResult:
        self.sent.append((device.endpoint, message))
        status = self.answers.get(device.endpoint, PushStatus.DELIVERED)
        http = {PushStatus.GONE: 410, PushStatus.REFUSED: 403, PushStatus.FAILED: 503}.get(status)
        return PushResult(status, "push.example.test", http)


def subscribe(session: Session, user: User, name: str) -> str:
    endpoint = f"https://push.example.test/{name}-{uuid4().hex[:8]}"
    push_subscriptions.upsert(
        session, user.id, endpoint=endpoint, p256dh="k", auth="a", user_agent=None
    )
    return endpoint


def save_report(
    session: Session,
    user: User,
    league_index: int = 0,
    *,
    kind: ReportKind = ReportKind.DAILY,
    day: date = DAY,
    cards: int = 3,
    title: str = "t",
) -> None:
    league = leagues.list_for_user(session, user.id)[league_index]
    saved_reports.save(
        session,
        ReportDocument(
            user_id=user.id, league_id=league.id, kind=kind, for_date=day, generated_at=FRIDAY,
            cards=tuple(
                Card(id=f"c{i}", kind=CardKind.RECAP, title=title, body="b") for i in range(cards)
            ),
        ),
    )  # fmt: skip


def notify(
    session: Session,
    user: User,
    sender: Sender,
    *,
    kind: ReportKind = ReportKind.DAILY,
    day: date = DAY,
    now: datetime = FRIDAY,
) -> NotifyOutcome:
    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session  # the test's one transaction stands in for the two short ones

    return notify_report_ready(
        same_session, lambda: sender, user_id=user.id, kind=kind, dates={day}, now=now
    )


def notified_at(session: Session, user: User, kind: ReportKind = ReportKind.DAILY) -> list[object]:
    return list(
        session.scalars(
            select(ReportRow.notified_at)
            .where(ReportRow.user_id == user.id, ReportRow.kind == kind.value)
            .order_by(ReportRow.league_id)
        )
    )


def test_migration_0008_adds_a_nullable_notified_at(engine: Engine, session: Session) -> None:
    column = next(c for c in inspect(engine).get_columns("reports") if c["name"] == "notified_at")
    assert column["nullable"] is True
    owner = setup_owner(session)
    save_report(session, owner)
    assert notified_at(session, owner) == [None]  # a report starts out not announced


def test_a_report_is_announced_once_and_a_rerun_is_quiet(session: Session) -> None:
    owner = setup_owner(session)
    subscribe(session, owner, "phone")
    save_report(session, owner, 0, cards=5)
    save_report(session, owner, 1, cards=4)
    sender = Sender()

    first = notify(session, owner, sender)
    assert (first.devices, first.delivered, first.problems) == (1, 1, ())
    ((_endpoint, message),) = sender.sent
    assert message.body == "9 cards in 2 leagues."  # counted from what is saved
    assert message.tag == "daily-2026-11-20"
    assert notified_at(session, owner) == [FRIDAY, FRIDAY]

    second = notify(session, owner, sender)  # the same day's rerun
    assert second.skipped == "already sent for these reports"
    assert len(sender.sent) == 1


def test_a_rerun_that_replaces_the_report_doesnt_announce_it_again(session: Session) -> None:
    """The report is rebuilt with new cards (a code fix, a late projection) but the push has
    gone out: `notified_at` survives the replacement."""
    owner = setup_owner(session)
    subscribe(session, owner, "phone")
    save_report(session, owner, title="before")
    sender = Sender()
    notify(session, owner, sender)

    save_report(session, owner, title="after", cards=7)  # replaced: same league, kind and date
    assert notified_at(session, owner) == [FRIDAY]
    assert notify(session, owner, sender).skipped == "already sent for these reports"
    assert len(sender.sent) == 1


def test_a_league_that_arrives_on_the_rerun_doesnt_start_a_second_push(session: Session) -> None:
    """A league failed in the morning run and its report is built by the rerun: one push per user,
    kind and date, so the day's announcement stays single."""
    owner = setup_owner(session)
    subscribe(session, owner, "phone")
    save_report(session, owner, 0)
    sender = Sender()
    notify(session, owner, sender)
    save_report(session, owner, 1)
    assert notify(session, owner, sender).skipped == "already sent for these reports"
    assert len(sender.sent) == 1


def test_each_kind_and_each_date_is_announced_on_its_own(session: Session) -> None:
    owner = setup_owner(session)
    subscribe(session, owner, "phone")
    sender = Sender()
    save_report(session, owner, kind=ReportKind.DAILY, day=DAY)
    save_report(session, owner, kind=ReportKind.DAILY, day=date(2026, 11, 21))
    save_report(session, owner, kind=ReportKind.WEEKLY, day=date(2026, 11, 23))
    assert notify(session, owner, sender, day=DAY).delivered == 1
    assert notify(session, owner, sender, day=date(2026, 11, 21)).delivered == 1  # the next day
    weekly = notify(
        session, owner, sender, kind=ReportKind.WEEKLY, day=date(2026, 11, 23), now=MONDAY
    )
    assert weekly.delivered == 1
    assert [m.tag for _e, m in sender.sent] == [
        "daily-2026-11-20", "daily-2026-11-21", "weekly-2026-11-23",
    ]  # fmt: skip


def test_the_weekly_overview_is_announced_on_mondays_only(session: Session) -> None:
    owner = setup_owner(session)
    subscribe(session, owner, "phone")
    save_report(session, owner, kind=ReportKind.WEEKLY, day=date(2026, 11, 20))
    sender = Sender()
    friday = notify(session, owner, sender, kind=ReportKind.WEEKLY, now=FRIDAY)
    assert friday.skipped is not None
    assert sender.sent == []
    assert notified_at(session, owner, ReportKind.WEEKLY) == [None]  # still unannounced, quietly
    assert notify(session, owner, sender, kind=ReportKind.WEEKLY, now=MONDAY).delivered == 1


def test_no_devices_means_no_push_no_warning_and_nothing_marked(session: Session) -> None:
    owner = setup_owner(session)
    save_report(session, owner)
    sender = Sender()
    outcome = notify(session, owner, sender)
    assert outcome.skipped == "no devices have notifications on"
    assert outcome.problems == ()
    assert sender.sent == []
    assert notified_at(session, owner) == [None]
    # A device that turns notifications on later is told about a rerun of that day: nothing was
    # announced yet, and nobody was promised one.
    subscribe(session, owner, "phone")
    assert notify(session, owner, sender).delivered == 1


def test_a_gone_device_is_deleted_and_the_others_still_count(session: Session) -> None:
    owner = setup_owner(session)
    lost = subscribe(session, owner, "old-phone")
    kept = subscribe(session, owner, "phone")
    save_report(session, owner)
    sender = Sender({lost: PushStatus.GONE})
    outcome = notify(session, owner, sender)
    assert (outcome.delivered, outcome.removed, outcome.problems) == (1, 1, ())
    assert push_subscriptions.endpoints_for_user(session, owner.id) == [kept]
    assert notified_at(session, owner) == [FRIDAY]
    row = session.scalar(select(PushSubscriptionRow).where(PushSubscriptionRow.endpoint == kept))
    assert row is not None
    assert row.last_success_at == FRIDAY  # when a push service last accepted something for it


def test_a_refused_push_warns_and_leaves_the_report_to_be_announced_by_a_rerun(
    session: Session,
) -> None:
    owner = setup_owner(session)
    phone = subscribe(session, owner, "phone")
    save_report(session, owner)
    refused = notify(session, owner, Sender({phone: PushStatus.REFUSED}))
    assert refused.delivered == 0
    assert len(refused.problems) == 1
    assert notified_at(session, owner) == [None]
    assert push_subscriptions.endpoints_for_user(session, owner.id) == [phone]  # kept: not lost
    fixed = notify(session, owner, Sender())  # the key was fixed; the rerun announces it
    assert fixed.delivered == 1


def test_one_users_push_never_touches_another_users_devices_or_reports(session: Session) -> None:
    ana, ben = setup_owner(session), setup_owner(session)
    ana_phone = subscribe(session, ana, "ana")
    ben_phone = subscribe(session, ben, "ben")
    save_report(session, ana)
    save_report(session, ben, cards=1)
    sender = Sender({ana_phone: PushStatus.GONE})
    notify(session, ana, sender)
    assert [e for e, _m in sender.sent] == [ana_phone]  # ben's phone was never addressed
    assert push_subscriptions.endpoints_for_user(session, ana.id) == []
    assert push_subscriptions.endpoints_for_user(session, ben.id) == [ben_phone]  # not deleted
    assert notified_at(session, ben) == [None]  # his report is still waiting for his own push
    ben_sender = Sender()
    notify(session, ben, ben_sender)
    assert ben_sender.sent[0][1].body == "1 card in 1 league."  # his own counts, not ana's
