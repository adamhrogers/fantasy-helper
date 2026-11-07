"""Run events: what reaches the phone, how loud, and that it can never break a run."""

from datetime import UTC, datetime, timedelta

from fantasy_helper.domain.ports import Alert, AlertPriority
from fantasy_helper.domain.runs import RunKind
from fantasy_helper.observability.events import RunEvents


class FakeNotifier:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class BrokenNotifier:
    def send(self, alert: Alert) -> None:
        raise ConnectionError("ntfy is down")


class StepClock:
    """Each reading is 30 seconds after the last."""

    def __init__(self) -> None:
        self.t = datetime(2026, 11, 7, 11, 30, tzinfo=UTC)

    def now(self) -> datetime:
        self.t += timedelta(seconds=30)
        return self.t


def events(progress: bool = True) -> tuple[RunEvents, FakeNotifier]:
    notifier = FakeNotifier()
    return (
        RunEvents(RunKind.DAILY, notifier, label="Daily run", progress=progress, clock=StepClock()),
        notifier,
    )


def test_a_healthy_run_is_quiet_from_start_to_finish() -> None:
    run, phone = events()
    run.started()
    run.step("Data loaded", "3 leagues.")
    run.step("Report built", "6 cards.")
    run.succeeded("Done in time.")
    assert [a.title for a in phone.alerts] == [
        "Daily run started",
        "Daily run: Data loaded",
        "Daily run: Report built",
        "Daily run succeeded",
    ]
    assert {a.priority for a in phone.alerts} == {AlertPriority.LOW}
    assert "Took 30 s." in phone.alerts[-1].message  # one clock tick between start and finish


def test_warnings_are_normal_and_failures_loud_and_say_where() -> None:
    run, phone = events()
    run.started()
    run.step("Data loaded")
    run.warned(["a rostered player is unmatched"], "Finished.")
    run.failed("ConnectError.", ["espn: HTTP 503"])
    warned, failed = phone.alerts[-2:]
    assert warned.priority is AlertPriority.DEFAULT
    assert "• a rostered player is unmatched" in warned.message
    assert failed.title == "Daily run failed"
    assert failed.priority is AlertPriority.HIGH
    assert "Last step done: Data loaded." in failed.message


def test_urgent_failures_and_custom_titles() -> None:
    run, phone = events()
    run.failed("Every dataset failed.", title="Morning snapshot failed", urgent=True)
    (alert,) = phone.alerts
    assert (alert.title, alert.priority) == ("Morning snapshot failed", AlertPriority.URGENT)


def test_switching_progress_off_keeps_warnings_and_failures() -> None:
    run, phone = events(progress=False)
    run.started()
    run.step("Data loaded")
    run.succeeded()
    assert phone.alerts == []
    run.warned(["x"])
    run.failed("y")
    assert [a.priority for a in phone.alerts] == [AlertPriority.DEFAULT, AlertPriority.HIGH]


def test_long_problem_lists_are_cut_short() -> None:
    run, phone = events()
    run.failed("Many problems.", [f"problem {i}" for i in range(20)])
    message = phone.alerts[0].message
    assert "problem 7" in message
    assert "problem 8" not in message
    assert "…and 12 more (see the run log)" in message


def test_a_broken_notifier_never_breaks_the_run() -> None:
    run = RunEvents(RunKind.INGEST, BrokenNotifier())
    run.started()
    run.failed("still fine")  # no exception


def test_a_retry_notice_is_normal_and_says_when() -> None:
    run, phone = events()
    run.retrying(2, 3, 120, "1 of 9 datasets failed in a way a retry can fix.", ["espn.season: x"])
    (alert,) = phone.alerts
    assert alert.title == "Daily run: retrying (attempt 2 of 3)"
    assert alert.priority is AlertPriority.DEFAULT
    assert alert.message.endswith("Trying again in 2 min.")
