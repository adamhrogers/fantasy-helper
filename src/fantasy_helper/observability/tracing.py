"""LangSmith tracing setup.

LangChain and LangGraph read LangSmith settings from environment variables. `configure_tracing`
copies them from `Settings` into this process's environment (never the parent shell), so every
model call, tool call and agent step is traced with no extra code (spec: Observability).
"""

import os
import time
import uuid
from typing import Any

from langsmith import Client, traceable, tracing_context

from fantasy_helper.config import Settings


def configure_tracing(settings: Settings) -> bool:
    """Turn LangSmith tracing on for this process if it is configured. Returns whether it is on."""
    if settings.langsmith_api_key is None or not settings.langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "false"
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    return True


def make_client(settings: Settings) -> Client:
    if settings.langsmith_api_key is None:
        raise ValueError("LANGSMITH_API_KEY is not set")
    return Client(
        api_key=settings.langsmith_api_key.get_secret_value(),
        api_url=settings.langsmith_endpoint,
    )


@traceable(name="fh.smoke_trace", run_type="chain")
def _smoke(check: str) -> dict[str, str]:
    return {"check": check, "status": "ok"}


def send_smoke_trace(settings: Settings, *, wait_seconds: float = 15.0) -> str:
    """Send one trace to LangSmith, wait for it to arrive and return its run id.

    Raises TimeoutError if the run is not readable back within `wait_seconds`.
    """
    client = make_client(settings)
    run_id = uuid.uuid4()
    with tracing_context(
        enabled=True,
        client=client,
        project_name=settings.langsmith_project,
        tags=["smoke", settings.env],
        metadata={"source": "fh doctor"},
    ):
        _smoke("doctor", langsmith_extra={"run_id": run_id})
    client.flush()

    deadline = time.monotonic() + wait_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            run: Any = client.read_run(run_id)
            return str(run.id)
        except Exception as exc:  # the run can take a moment to become readable
            last_error = exc
            time.sleep(1.5)
    raise TimeoutError(f"trace {run_id} not visible in LangSmith yet") from last_error
