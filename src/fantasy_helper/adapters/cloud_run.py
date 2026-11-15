"""Start a Cloud Run job from inside another one: how the morning chain hands over (ADR 0028).

A job runs as its own service account, and Google's metadata server hands that account's access
token to anything running in the container. This asks the Cloud Run API to start the next job with
it: no key file, no stored credential. The account has `run.invoker` on that one job and nothing
else (scripts/gcp-setup.sh), so a compromised job can start the next link in the chain, never
change what it runs.

The token is never logged, and errors carry a kind, never a response body.
"""

import re
import time
from collections.abc import Callable

import httpx

METADATA_ACCESS_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
RUN_API = "https://run.googleapis.com/v2"
# projects/<id>/locations/<region>/jobs/<name>: checked, because it becomes part of a URL.
JOB_RESOURCE = re.compile(
    r"^projects/[a-z][a-z0-9-]{3,28}[a-z0-9]/locations/[a-z0-9-]+/jobs/[a-z][a-z0-9-]{0,62}$"
)
ATTEMPTS = 3
WAIT_SECONDS = 5.0


class CloudRunError(Exception):
    """The next job couldn't be started. `kind` is safe to show: no token, no response text."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def start_job(
    resource: str,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Start one execution of the job, as this process's own service account. Retries a blip."""
    if not JOB_RESOURCE.fullmatch(resource):
        raise CloudRunError("FH_NEXT_JOB isn't a Cloud Run job name")
    http = client or httpx.Client(timeout=15.0)
    last = "unknown"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            token = http.get(METADATA_ACCESS_URL, headers={"Metadata-Flavor": "Google"})
            token.raise_for_status()
            response = http.post(
                f"{RUN_API}/{resource}:run",
                headers={"Authorization": f"Bearer {token.json()['access_token']}"},
                json={},
            )
            response.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last = f"HTTP {status}"
            if status in (400, 401, 403, 404):  # a wrong name or missing permission won't clear up
                raise CloudRunError(last) from exc
        except (httpx.TransportError, KeyError, ValueError) as exc:
            last = type(exc).__name__
        if attempt < ATTEMPTS:
            sleep(WAIT_SECONDS)
    raise CloudRunError(last)
