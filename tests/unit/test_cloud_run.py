"""Starting the next job in the morning chain, as the job's own service account (ADR 0028)."""

import httpx
import pytest

from fantasy_helper.adapters.cloud_run import (
    METADATA_ACCESS_URL,
    CloudRunError,
    start_job,
)

JOB = "projects/fantasy-helper-123456/locations/us-east4/jobs/daily"
TOKEN = "ya29.very-secret-token"  # noqa: S105 (a fake, to prove it is never shown)


def client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def healthy(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == METADATA_ACCESS_URL:
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3000})
        return httpx.Response(200, json={"name": "operations/abc"})

    return httpx.MockTransport(handle)


def test_the_job_is_started_with_this_accounts_own_token() -> None:
    seen: list[httpx.Request] = []
    start_job(JOB, client=client(healthy(seen)), sleep=lambda _s: None)
    token_call, run_call = seen
    assert token_call.headers["Metadata-Flavor"] == "Google"
    assert run_call.method == "POST"
    assert str(run_call.url) == f"https://run.googleapis.com/v2/{JOB}:run"
    assert run_call.headers["Authorization"] == f"Bearer {TOKEN}"
    assert run_call.content == b"{}"  # no overrides: the job runs exactly what it was deployed to


@pytest.mark.parametrize(
    "resource",
    [
        "daily",
        "projects/p/locations/us-east4/jobs/daily",  # a project id is 6 to 30 characters
        "projects/fantasy-helper-123456/locations/us-east4/jobs/daily:run?x=1",
        "projects/fantasy-helper-123456/locations/us-east4/jobs/../../x",
        "https://evil.example/projects/fantasy-helper-123456/locations/us-east4/jobs/daily",
    ],
)
def test_a_value_that_isnt_a_job_name_never_reaches_a_url(resource: str) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(CloudRunError, match="isn't a Cloud Run job name"):
        start_job(resource, client=client(healthy(seen)), sleep=lambda _s: None)
    assert seen == []  # not even the token was asked for


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_wrong_name_or_missing_permission_fails_at_once(status: int) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if str(request.url) == METADATA_ACCESS_URL:
            return httpx.Response(200, json={"access_token": TOKEN})
        return httpx.Response(status, text="response body that must never be shown")

    with pytest.raises(CloudRunError) as raised:
        start_job(JOB, client=client(httpx.MockTransport(handle)), sleep=lambda _s: None)
    assert raised.value.kind == f"HTTP {status}"
    assert len([c for c in calls if "run" in c]) == 1  # no pointless retries
    assert "never be shown" not in str(raised.value)
    assert TOKEN not in str(raised.value)


def test_a_blip_is_retried_and_a_lasting_outage_gives_up() -> None:
    attempts = {"run": 0}
    waits: list[float] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        if str(request.url) == METADATA_ACCESS_URL:
            return httpx.Response(200, json={"access_token": TOKEN})
        attempts["run"] += 1
        return httpx.Response(503) if attempts["run"] < 3 else httpx.Response(200, json={})

    start_job(JOB, client=client(httpx.MockTransport(flaky)), sleep=waits.append)
    assert attempts["run"] == 3
    assert len(waits) == 2

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(CloudRunError) as raised:
        start_job(JOB, client=client(httpx.MockTransport(down)), sleep=lambda _s: None)
    assert raised.value.kind == "ConnectError"


def test_off_google_cloud_there_is_no_token_and_it_says_so() -> None:
    """On the laptop the metadata server doesn't exist. FH_NEXT_JOB isn't set there, so this can't
    happen by accident, but a misconfiguration must fail loudly, with a kind and nothing else."""

    def no_metadata(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name not known: metadata.google.internal", request=request)

    with pytest.raises(CloudRunError) as raised:
        start_job(JOB, client=client(httpx.MockTransport(no_metadata)), sleep=lambda _s: None)
    assert raised.value.kind == "ConnectError"
    assert "metadata" not in str(raised.value)
