"""`fh push test`: a test notification to the owner's devices, and an answer a person can act on
(ADR 0030). It runs as the `daily` job with its command replaced, so it must never behave like a
morning run."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from fantasy_helper.cli import push as cli_push
from fantasy_helper.cli.main import app
from fantasy_helper.delivery.push import PushNotConfiguredError, PushResult, PushStatus
from fantasy_helper.domain.ports import Alert
from fantasy_helper.domain.push import PushDevice, PushMessage
from fantasy_helper.domain.users import Role, User
from fantasy_helper.storage.repositories import push_subscriptions, users

runner = CliRunner()
OWNER = User(email="a@example.com", display_name="A", timezone="America/New_York", role=Role.OWNER)
ENDPOINT = "https://web.push.apple.com/AbCd1234SecretPart"


class Sender:
    def __init__(self, *results: PushResult) -> None:
        self.results = list(results)
        self.sent: list[PushMessage] = []

    def send(self, _device: PushDevice, message: PushMessage) -> PushResult:
        self.sent.append(message)
        return self.results[len(self.sent) - 1]


def result(status: PushStatus, http: int | None = None, reason: str | None = None) -> PushResult:
    return PushResult(status, "web.push.apple.com", http, reason)


class World:
    def __init__(self) -> None:
        self.devices = [PushDevice(id=uuid4(), endpoint=ENDPOINT, p256dh="k", auth="a")]
        self.sender: Sender | PushNotConfiguredError = Sender(result(PushStatus.DELIVERED))
        self.alerts: list[Alert] = []
        self.recorded: list[list[PushStatus]] = []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    w = World()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@localhost/x")
    monkeypatch.setenv("FH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FH_NEXT_JOB", raising=False)

    @contextmanager
    def fake_transaction(_url: str) -> Iterator[object]:
        yield object()

    class Recorder:
        def send(self, alert: Alert) -> None:
            w.alerts.append(alert)

    def fake_sender(_settings: object) -> Sender:
        if isinstance(w.sender, PushNotConfiguredError):
            raise w.sender
        return w.sender

    def fake_record(_s: object, _user: User, results: Any, _now: datetime) -> int:
        w.recorded.append([r.status for _d, r in results])
        return 0

    monkeypatch.setattr(cli_push, "transaction", fake_transaction)
    monkeypatch.setattr(users, "get_owner", lambda _s: OWNER)
    monkeypatch.setattr(push_subscriptions, "devices_for_user", lambda _s, _id: w.devices)
    monkeypatch.setattr(cli_push, "sender_from_settings", fake_sender)
    monkeypatch.setattr(cli_push, "record", fake_record)
    monkeypatch.setattr(cli_push, "notifier_from_settings", lambda _settings: Recorder())
    return w


def test_a_test_notification_reaches_every_device_and_says_so(world: World) -> None:
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 0, outcome.output
    assert "✓ device 1: web.push.apple.com delivered" in outcome.output
    assert "1 device(s) notified: check your phone" in outcome.output
    assert isinstance(world.sender, Sender)
    (message,) = world.sender.sent
    assert (message.title, message.tag) == ("Test notification", "test")
    assert world.recorded == [[PushStatus.DELIVERED]]  # the same bookkeeping a real push does
    (alert,) = world.alerts  # the answer also reaches the ops topic: --wait only says passed/failed
    assert alert.title == "Push test: sent"


def test_a_refusal_is_explained_and_the_command_fails(world: World) -> None:
    world.sender = Sender(result(PushStatus.REFUSED, 403, "BadJwtToken"))
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 1
    assert "web.push.apple.com refused HTTP 403 BadJwtToken" in outcome.output
    assert "fh-app-origins" in outcome.output
    assert "vapid-setup.sh --rotate" in outcome.output
    assert ENDPOINT not in outcome.output  # an endpoint is the device's address: never printed
    assert "AbCd1234SecretPart" not in world.alerts[0].message
    assert world.alerts[0].title == "Push test: not delivered"


def test_a_lost_device_is_reported_and_deleted(world: World) -> None:
    world.sender = Sender(result(PushStatus.GONE, 410))
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 1  # nothing was delivered
    assert "gone" in outcome.output
    assert "tap Enable notifications again" in outcome.output
    assert world.recorded == [[PushStatus.GONE]]  # record() deletes it


def test_an_unreachable_push_service_says_to_try_again(world: World) -> None:
    world.sender = Sender(PushResult(PushStatus.FAILED, "web.push.apple.com", None, "Timeout"))
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 1
    assert "run it again in a minute" in outcome.output


def test_no_devices_says_what_to_do_in_the_app(world: World) -> None:
    world.devices = []
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 1
    assert "tap Enable notifications" in outcome.output
    assert world.alerts[0].title == "Push test: not delivered"


def test_missing_push_settings_name_the_setting(world: World) -> None:
    world.sender = PushNotConfiguredError("FH_VAPID_PRIVATE_KEY isn't set")
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 1
    assert "FH_VAPID_PRIVATE_KEY isn't set" in outcome.output


def test_one_good_device_beside_a_refused_one_still_fails_the_command(world: World) -> None:
    """Every device must be notified for a pass: a half-working setup shouldn't look fine."""
    world.devices.append(PushDevice(id=uuid4(), endpoint=ENDPOINT + "2", p256dh="k", auth="a"))
    world.sender = Sender(result(PushStatus.DELIVERED), result(PushStatus.REFUSED, 403))
    outcome = runner.invoke(app, ["push", "test"])
    assert outcome.exit_code == 1
    assert "✓ device 1" in outcome.output
    assert "✗ device 2" in outcome.output


def test_it_is_not_a_morning_run() -> None:
    """It replaces the `daily` job's command, so it must not ping the daily watchdog or hand over
    to the weekly job (the job has FH_NEXT_JOB set), and a run's ops events aren't its business."""
    source = Path(cli_push.__file__).read_text()
    for forbidden in ("start_next", "watched", "heartbeat", "next_job", "RunEvents"):
        assert forbidden not in source, forbidden


def test_the_command_is_registered_under_push() -> None:
    assert "test" in runner.invoke(app, ["push", "--help"]).output
