"""Can we rerun this? The rules that decide whether a failed part of a run gets retried."""

import json

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.adapters.espn.mapper import EspnMappingError
from fantasy_helper.adapters.sleeper.league import SleeperLookupError
from fantasy_helper.domain.runs import FailureKind, RunKind
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.pipeline.retry import (
    MAX_ATTEMPTS,
    RunStoppedError,
    classify_exception,
    classify_status,
    outcome,
    run_with_retries,
    wait_before,
)
from fantasy_helper.storage.raw_store import RawStoreError


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (429, FailureKind.RATE_LIMITED, True),
        (500, FailureKind.SERVER_ERROR, True),
        (503, FailureKind.SERVER_ERROR, True),
        (401, FailureKind.AUTH, False),
        (403, FailureKind.AUTH, False),
        (404, FailureKind.NOT_FOUND, False),
        (400, FailureKind.BAD_RESPONSE, True),
    ],
)
def test_http_statuses(status: int, kind: FailureKind, retryable: bool) -> None:
    assert classify_status(status) is kind
    assert kind.retryable is retryable


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (httpx.ConnectError("refused"), FailureKind.NETWORK),
        (httpx.ReadTimeout("slow"), FailureKind.NETWORK),
        (EspnAccessError("cookies", 401), FailureKind.AUTH),
        (EspnAccessError("busy", 503), FailureKind.SERVER_ERROR),
        (SleeperLookupError("gone", 404), FailureKind.NOT_FOUND),
        (OperationalError("SELECT 1", {}, Exception("down")), FailureKind.DATABASE),
        (EspnMappingError("new shape"), FailureKind.FORMAT_CHANGED),
        (json.JSONDecodeError("bad", "{", 0), FailureKind.BAD_RESPONSE),
        (RawStoreError("refusing to overwrite"), FailureKind.STORAGE),
        (KeyError("oops"), FailureKind.BUG),
    ],
)
def test_exceptions(exc: Exception, kind: FailureKind) -> None:
    assert classify_exception(exc) is kind


def test_only_causes_that_can_clear_up_are_retried() -> None:
    retryable = {k for k in FailureKind if k.retryable}
    assert retryable == {
        FailureKind.NETWORK, FailureKind.SERVER_ERROR, FailureKind.RATE_LIMITED,
        FailureKind.BAD_RESPONSE, FailureKind.MISSING_INPUT, FailureKind.DATABASE,
    }  # fmt: skip


def test_three_attempts_two_then_five_minutes_apart() -> None:
    assert MAX_ATTEMPTS == 3
    assert (wait_before(2), wait_before(3)) == (120.0, 300.0)


def test_outcomes_read_plainly() -> None:
    assert outcome(FailureKind.SERVER_ERROR, 3) == "the source's servers erred, after 3 attempts"
    assert outcome(FailureKind.AUTH, 1).endswith("not retried (retrying can't fix it)")
    assert all(k.describe for k in FailureKind)


# ------------------------------------------------------------------- the shared retry loop


class _Phone:
    def __init__(self) -> None:
        self.titles: list[str] = []

    def send(self, alert: object) -> None:
        self.titles.append(alert.title)  # type: ignore[attr-defined]


def _events() -> tuple[RunEvents, _Phone]:
    phone = _Phone()
    return RunEvents(RunKind.DAILY, phone, label="Daily run"), phone


def test_a_retryable_crash_runs_again_then_succeeds() -> None:
    events, phone = _events()
    calls: dict[str, int] = {"n": 0}
    waits: list[float] = []

    def once() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("offline")
        return "done"

    assert run_with_retries(once, events, sleep=waits.append) == ("done", 2)
    assert waits == [120.0]
    assert phone.titles == ["Daily run: retrying (attempt 2 of 3)"]


def test_a_crash_retrying_cant_fix_stops_at_once() -> None:
    events, _ = _events()
    waits: list[float] = []

    def once() -> str:
        raise EspnAccessError("cookies", 401)

    with pytest.raises(RunStoppedError) as stopped:
        run_with_retries(once, events, sleep=waits.append)
    assert (stopped.value.kind, stopped.value.attempts, waits) == (FailureKind.AUTH, 1, [])
    assert isinstance(stopped.value.__cause__, EspnAccessError)


def test_failed_parts_rerun_until_attempts_run_out() -> None:
    events, phone = _events()
    waits: list[float] = []
    result, attempts = run_with_retries(
        lambda: "partial", events, retryable_parts=lambda _r: 1, sleep=waits.append
    )
    assert (result, attempts, waits) == ("partial", 3, [120.0, 300.0])
    assert len(phone.titles) == 2


def test_the_callers_own_exceptions_pass_straight_through() -> None:
    events, _ = _events()

    def once() -> str:
        raise LookupError("not ours to retry")

    with pytest.raises(LookupError):
        run_with_retries(once, events, passthrough=(LookupError,), sleep=lambda _s: None)
