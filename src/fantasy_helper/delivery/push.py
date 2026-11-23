"""Sending Web Push (ADR 0030): the sender, the messages, and the VAPID keys.

`PushSender` is the port the rest of the system uses; `WebPushSender` is the one real
implementation, on `pywebpush` (2.5.0). Everything about the library that a mistake would hide is
pinned by a test against the real library and a fake push service:

- a push service takes a VAPID key only as the string the library reads, so the private key is the
  raw 32-byte key in URL-safe base64, which also fits in one environment variable;
- `webpush()` writes `aud` and `exp` into the claims dict it is given, so every send builds a
  fresh dict (a reused one would keep the first push service's `aud` for every later device: Apple
  refuses it while Google doesn't care);
- `webpush()`'s timeout is `None` (no timeout) unless one is passed, and it follows redirects
  unless the session it is given won't;
- a refusal comes back as an exception carrying the response, and a failure to connect as a bare
  `requests` error.

A message carries counts and a path, never a card's text or a league's name (domain/push.py).
"""

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Protocol

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from fantasy_helper.config import Settings
from fantasy_helper.config.settings import app_address
from fantasy_helper.domain.push import (
    PushDevice,
    PushMessage,
    PushPath,
    endpoint_host,
    is_push_service,
)
from fantasy_helper.domain.reports import ReportKind
from fantasy_helper.observability.logs import get_logger

log = get_logger(__name__)

# How long a push service keeps a message for a phone that is off or out of range. After that the
# report is old news (the app shows it anyway), so a morning push never turns up in the evening.
TTL_SECONDS = 6 * 60 * 60
# Per request, for connecting and for each read. A push service answers in well under a second;
# pipeline/notify.py also caps each send, and the whole push, by the clock, because a server that
# trickles its answer a byte at a time never trips a per-read timeout.
TIMEOUT_SECONDS = 5.0


class PushStatus(StrEnum):
    DELIVERED = "delivered"  # the push service accepted it for the device
    GONE = "gone"  # 404 or 410, or keys that can never work: the device is lost, delete it
    BLOCKED = "blocked"  # not an address on a known push service: never sent to, deleted
    REFUSED = "refused"  # 400, 401, 403, 413: our key, subject or message is wrong for everyone
    FAILED = "failed"  # the push service is down or limiting us, or we couldn't reach it: later


@dataclass(frozen=True)
class PushResult:
    status: PushStatus
    host: str  # the push service (never the endpoint: the whole address is the device's secret)
    http_status: int | None = None
    reason: str | None = None  # the push service's own short reason, e.g. Apple's BadJwtToken

    def describe(self) -> str:
        """One line for a log or the terminal: who, what, and why. No endpoint, no key."""
        bits = [self.host, self.status.value]
        if self.http_status is not None:
            bits.append(f"HTTP {self.http_status}")
        if self.reason:
            bits.append(self.reason)
        return " ".join(bits)


class PushSender(Protocol):
    def send(self, device: PushDevice, message: PushMessage) -> PushResult: ...


class PushNotConfiguredError(Exception):
    """Push can't be sent from here, and why (safe to show: it names a setting, never a value)."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class VapidKeys:
    """A P-256 key pair in the forms the world uses: the raw private key (what the report jobs
    read from one environment variable) and the uncompressed public point (what the browser's
    `applicationServerKey` takes), both URL-safe base64 without padding."""

    private_key: str = field(repr=False)  # a secret: never printed
    public_key: str


def public_key_of(private_key: str) -> str:
    """The public key that goes with a private one, read the way the sender reads it."""
    vapid = Vapid.from_string(private_key)
    point = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _b64(point)


def generate_vapid_keys() -> VapidKeys:
    """A fresh pair. Checked before it is returned: the library must accept the private key as a
    string and derive the same public key, or nothing is stored (scripts/vapid-setup.sh)."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public = _b64(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    if public_key_of(private) != public:
        raise RuntimeError("the push library doesn't read this key the way it was written")
    return VapidKeys(private_key=private, public_key=public)


def _usable(device: PushDevice) -> bool:
    """Keys a browser could have made: a 65-byte uncompressed P-256 point and a 16-byte secret.
    Anything else can never be encrypted to, so it is a lost device, not a failed send."""
    try:
        point = _unb64(device.p256dh)
        secret = _unb64(device.auth)
    except ValueError:
        return False
    return len(point) == 65 and point[0] == 4 and len(secret) == 16


_REASON = re.compile(r"[A-Za-z0-9_ .:-]{1,60}")


def _reason(exc: WebPushException) -> str | None:
    """The short reason a push service gives in its error body (Apple: `{"reason": "..."}`).
    Only a plain short token is kept: nothing else from a response goes to a log."""
    try:
        body = exc.response.json()
    except Exception:  # not JSON, or no response at all
        return None
    reason = body.get("reason") if isinstance(body, Mapping) else None
    return reason if isinstance(reason, str) and _REASON.fullmatch(reason) else None


class WebPushSender:
    def __init__(
        self,
        vapid: Vapid,
        subject: str,
        *,
        session: Any = None,  # a requests.Session; tests give one with a fake push service on it
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._vapid = vapid
        self._subject = subject
        self._session = session
        self._timeout = timeout

    def _new_session(self) -> Any:
        """A session that never follows a redirect: a push service answers where it was asked,
        so a redirect would only send the push, and our signed token, somewhere else."""
        session = self._session if self._session is not None else requests.Session()
        session.max_redirects = 0
        return session

    def send(self, device: PushDevice, message: PushMessage) -> PushResult:
        host = endpoint_host(device.endpoint)
        # Checked again here, not only when the API saved it: a row written before the list
        # existed, or by anything but the API, must not send a request off to some other server.
        if not is_push_service(device.endpoint):
            return PushResult(PushStatus.BLOCKED, host, reason="not a known push service")
        if not _usable(device):
            return PushResult(PushStatus.GONE, host, reason="malformed subscription keys")
        session = self._new_session()
        try:
            webpush(
                subscription_info={
                    "endpoint": device.endpoint,
                    "keys": {"p256dh": device.p256dh, "auth": device.auth},
                },
                data=message.payload(),
                vapid_private_key=self._vapid,
                # Fresh every time: webpush() writes `aud` (this device's push service) and `exp`
                # into the dict it is given.
                vapid_claims={"sub": self._subject},
                ttl=TTL_SECONDS,
                timeout=self._timeout,
                requests_session=session,
            )
        except WebPushException as exc:
            status = exc.status_code
            if status in (404, 410):
                return PushResult(PushStatus.GONE, host, status, _reason(exc))
            if status in (400, 401, 403, 413):
                return PushResult(PushStatus.REFUSED, host, status, _reason(exc))
            return PushResult(PushStatus.FAILED, host, status, _reason(exc))
        except Exception as exc:  # couldn't connect, timed out, redirected: requests' own errors
            return PushResult(PushStatus.FAILED, host, reason=type(exc).__name__)
        finally:
            if session is not self._session:
                session.close()
        return PushResult(PushStatus.DELIVERED, host)


def sender_from_settings(settings: Settings) -> WebPushSender:
    """The sender the report jobs use, or `PushNotConfiguredError` saying which setting is wrong.
    The key comes from `FH_VAPID_PRIVATE_KEY`, the subject from the app's own address (the first of
    `FH_APP_ORIGINS`, the same secret the API's CORS list is read from)."""
    if settings.vapid_private_key is None:
        raise PushNotConfiguredError(
            "FH_VAPID_PRIVATE_KEY isn't set (scripts/vapid-setup.sh makes it)"
        )
    subject = app_address(settings)
    if subject is None:
        raise PushNotConfiguredError(
            "FH_APP_ORIGINS has no https address for the app yet: Apple refuses a push whose "
            "subject isn't a URL (docs/runbooks/phone-app.md, Part 2, step 4)"
        )
    try:
        vapid = Vapid.from_string(settings.vapid_private_key.get_secret_value())
    except Exception as exc:  # the library's own errors vary; none carries a usable message
        raise PushNotConfiguredError(
            f"FH_VAPID_PRIVATE_KEY isn't a key the push library reads ({type(exc).__name__}): "
            "make a new pair with scripts/vapid-setup.sh --rotate"
        ) from exc
    return WebPushSender(vapid, subject)


# ------------------------------------------------------------------------------ the messages


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def report_ready(kind: ReportKind, for_date: date, *, reports: int, cards: int) -> PushMessage:
    """ "Today's report is ready" (or the week's overview), with counts and where a tap goes.
    The tag names the kind and the date, so a second push for the same report replaces the first
    on the screen instead of stacking."""
    title: str
    path: PushPath
    if kind is ReportKind.DAILY:
        title, path = "Today's report is ready", "/today"
    elif kind is ReportKind.WEEKLY:
        title, path = "This week's overview is ready", "/week"
    else:
        raise ValueError(f"no push is sent for a {kind.value} report")
    body = f"{_count(cards, 'card')} in {_count(reports, 'league')}."
    return PushMessage(
        title=title, body=body, path=path, tag=f"{kind.value}-{for_date.isoformat()}"
    )


def diagnostic_message() -> PushMessage:
    """What `fh push test` sends. Its own tag, so it never replaces a real report's notification."""
    return PushMessage(
        title="Test notification",
        body="If you can read this, push works.",
        path="/today",
        tag="test",
    )
