"""Can we rerun this? The rules that decide whether a failed part of a run gets retried."""

import json

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.adapters.espn.mapper import EspnMappingError
from fantasy_helper.adapters.sleeper.league import SleeperLookupError
from fantasy_helper.domain.runs import FailureKind
from fantasy_helper.pipeline.retry import (
    MAX_ATTEMPTS,
    classify_exception,
    classify_status,
    outcome,
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
