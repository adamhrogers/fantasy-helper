"""`fh push test`: one test notification to your own devices, and an answer you can read.

In the cloud it is the `daily` job with its command replaced, so it has the VAPID key and the
database and nothing else needs setting up:

    gcloud run jobs execute daily --region us-east4 --args=push,test --wait

It sends and exits. It isn't a morning run: no watchdog ping, no hand-over to `weekly`, nothing
written but the same bookkeeping a real push does (a device the push service says is gone is
deleted). The result is printed (it lands in the job's log) and sent to the ops topic, because
`--wait` only says whether the job passed. ADR 0030.
"""

import typer

from fantasy_helper.cli._common import fail, transaction
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.delivery.push import (
    PushNotConfiguredError,
    PushResult,
    PushStatus,
    diagnostic_message,
    sender_from_settings,
)
from fantasy_helper.domain.ports import Alert, AlertPriority, SystemClock
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.observability.logs import get_logger
from fantasy_helper.pipeline.notify import deliver, record
from fantasy_helper.storage.repositories import push_subscriptions, users

log = get_logger(__name__)

app = typer.Typer(help="Web Push: check that your phone can be notified.", no_args_is_help=True)

_MEANING = {
    PushStatus.DELIVERED: "the push service accepted it",
    PushStatus.GONE: "the push service says this device's subscription is gone, so it was "
    "deleted: open the app and tap Enable notifications again",
    PushStatus.REFUSED: "the push service refused our credentials: the app's address in "
    "fh-app-origins, or a VAPID key that isn't the pair the app subscribed with (after "
    "scripts/vapid-setup.sh --rotate every device must turn notifications off and on again)",
    PushStatus.FAILED: "the push service couldn't be reached or is having trouble: run it again "
    "in a minute",
}


def _tell_phone(settings: Settings, delivered: bool, lines: list[str]) -> None:
    """The result on the ops topic: a run's `--wait` only says passed or failed. Names push
    services and HTTP statuses, never an endpoint or a key."""
    try:
        notifier_from_settings(settings).send(
            Alert(
                title="Push test: sent" if delivered else "Push test: not delivered",
                message="\n".join(lines)[:2000] or "No devices.",
                priority=AlertPriority.DEFAULT if delivered else AlertPriority.HIGH,
                tags=("white_check_mark" if delivered else "x",),
            )
        )
    except Exception as exc:  # the answer is in the log and the exit code either way
        log.error("push_test_alert_failed", error=type(exc).__name__)


def _line(number: int, result: PushResult) -> str:
    mark = "✓" if result.status is PushStatus.DELIVERED else "✗"
    return f"{mark} device {number}: {result.describe()}: {_MEANING[result.status]}"


@app.command("test")
def send_test() -> None:
    """Send a test notification to every device of the owner and say what each push service did.
    Exits 0 only if every device was notified."""
    settings = get_settings()
    if settings.database_url is None:
        raise fail("DATABASE_URL is not set (see .env.example)")
    url = settings.database_url.get_secret_value()
    with transaction(url) as session:
        owner = users.get_owner(session)
        devices = push_subscriptions.devices_for_user(session, owner.id) if owner else []
    if owner is None:
        raise fail("no user yet: run `fh league add` first")
    if not devices:
        message = (
            "no device has notifications on: open the app from the Home Screen, tap Enable "
            "notifications (at the bottom of Today), then run this again"
        )
        _tell_phone(settings, False, [message])
        raise fail(message)
    try:
        sender = sender_from_settings(settings)
    except PushNotConfiguredError as exc:
        _tell_phone(settings, False, [f"Not sent: {exc}"])
        raise fail(f"{exc}") from exc

    results = deliver(sender, devices, diagnostic_message())
    with transaction(url) as session:
        record(session, owner, results, SystemClock().now())
    lines = [_line(i, result) for i, (_device, result) in enumerate(results, start=1)]
    for line in lines:
        typer.echo(line)
    delivered = all(result.status is PushStatus.DELIVERED for _d, result in results)
    _tell_phone(settings, delivered, lines)
    if not delivered:
        raise typer.Exit(code=1)
    typer.echo(f"✓ {len(results)} device(s) notified: check your phone")
