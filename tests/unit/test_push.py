"""Sending Web Push (ADR 0030): the real `pywebpush` against a fake push service, so what a phone
would receive is checked end to end: the key format, the VAPID signature, the encryption, the
headers, and what each answer from a push service means."""

import base64
import json
import os
from datetime import date
from typing import Any
from uuid import uuid4

import http_ece
import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pydantic import ValidationError

from fantasy_helper.config import Settings
from fantasy_helper.config.settings import app_address
from fantasy_helper.delivery.push import (
    TIMEOUT_SECONDS,
    TTL_SECONDS,
    PushNotConfiguredError,
    PushStatus,
    WebPushSender,
    diagnostic_message,
    generate_vapid_keys,
    public_key_of,
    report_ready,
    sender_from_settings,
)
from fantasy_helper.domain.push import (
    MAX_PAYLOAD_BYTES,
    PushDevice,
    PushMessage,
    endpoint_host,
    is_push_service,
)
from fantasy_helper.domain.reports import ReportKind

SUBJECT = "https://app.example.test"
APPLE = "https://web.push.apple.com/AbCd1234"
GOOGLE = "https://fcm.googleapis.com/fcm/send/AbCd1234"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def body_of(request: requests.PreparedRequest) -> bytes:
    body = request.body
    assert isinstance(body, bytes)
    return body


def header(request: requests.PreparedRequest, name: str) -> str:
    value = request.headers[name]
    assert isinstance(value, str)
    return value


class Phone:
    """What a browser holds for one subscription: its own key pair and the auth secret. The
    app sends the public half and the secret to the server; the private half decrypts."""

    def __init__(self, endpoint: str = APPLE) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        point = self.key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        self.device = PushDevice(
            id=uuid4(), endpoint=endpoint, p256dh=b64(point), auth=b64(self.auth)
        )

    def read(self, request: requests.PreparedRequest) -> dict[str, str]:
        plain = http_ece.decrypt(
            body_of(request), private_key=self.key, auth_secret=self.auth, version="aes128gcm"
        )
        return json.loads(plain)  # type: ignore[no-any-return]


class FakePushService(requests.adapters.BaseAdapter):
    """Stands in for Apple's, Google's or Mozilla's push service: records what arrives and
    answers with whatever status the test wants."""

    def __init__(
        self,
        status: int = 201,
        body: bytes = b"",
        error: Exception | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.status = status
        self.body = body
        self.error = error
        self.headers = headers or {}
        self.received: list[tuple[requests.PreparedRequest, Any]] = []

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        self.received.append((request, timeout))
        if self.error is not None:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        response._content = self.body  # what the adapter would have read off the wire
        response.headers.update(self.headers)
        response.url = request.url or ""
        response.request = request
        return response

    def close(self) -> None:
        pass


def sender_with(service: FakePushService, keys: Any = None) -> WebPushSender:
    keys = keys or generate_vapid_keys()
    session = requests.Session()
    session.mount("https://", service)
    return WebPushSender(Vapid.from_string(keys.private_key), SUBJECT, session=session)


MESSAGE = PushMessage(title="Today's report is ready", body="8 cards in 1 league.",
                      path="/today", tag="daily-2026-11-20")  # fmt: skip


# ----------------------------------------------------------------------------------- the keys


def test_a_generated_key_pair_is_in_the_forms_the_library_and_the_browser_take() -> None:
    keys = generate_vapid_keys()
    assert len(unb64(keys.private_key)) == 32  # the raw private key: fits in one environment value
    point = unb64(keys.public_key)
    assert len(point) == 65  # an uncompressed P-256 point: applicationServerKey
    assert point[0] == 4
    assert "=" not in keys.private_key + keys.public_key  # URL-safe, no padding
    # The library reads the private key as a plain string (the way an env var arrives), and finds
    # the same public key.
    assert public_key_of(keys.private_key) == keys.public_key
    assert Vapid.from_string(keys.private_key).public_key is not None


def test_the_private_key_never_shows_in_a_repr_or_a_log_line() -> None:
    keys = generate_vapid_keys()
    assert keys.private_key not in repr(keys)
    assert keys.public_key in repr(keys)


def test_two_generated_pairs_differ() -> None:
    assert generate_vapid_keys().private_key != generate_vapid_keys().private_key


# ------------------------------------------------------------------- what a phone receives


def test_a_push_is_readable_by_the_phone_signed_by_us_and_kept_six_hours() -> None:
    keys = generate_vapid_keys()
    service = FakePushService()
    phone = Phone()
    result = sender_with(service, keys).send(phone.device, MESSAGE)
    assert result.status is PushStatus.DELIVERED
    ((request, timeout),) = service.received

    # The phone can decrypt exactly the message: counts and a path, nothing else.
    assert phone.read(request) == {
        "title": "Today's report is ready",
        "body": "8 cards in 1 league.",
        "path": "/today",
        "tag": "daily-2026-11-20",
    }
    assert header(request, "TTL") == str(TTL_SECONDS) == str(6 * 60 * 60)
    assert header(request, "Content-Encoding") == "aes128gcm"
    assert len(body_of(request)) < MAX_PAYLOAD_BYTES
    # A short timeout is always passed: the library's own default is none at all.
    assert timeout == TIMEOUT_SECONDS
    assert TIMEOUT_SECONDS <= 5

    # The VAPID header is a token signed with our key, for this push service, naming the app.
    auth = header(request, "Authorization")
    assert auth.startswith("vapid t=")
    token, k = auth.removeprefix("vapid t=").split(",k=")
    assert k == keys.public_key
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(keys.public_key))
    claims = jwt.decode(token, public, algorithms=["ES256"], audience="https://web.push.apple.com")
    assert claims["sub"] == SUBJECT
    assert claims["exp"] > 0


def test_each_device_is_signed_for_its_own_push_service() -> None:
    """The library writes `aud` into the claims it is given: a dict shared between sends would
    keep the first service's address, and Apple refuses a token meant for Google (the reverse
    works, which would hide the bug until the first iPhone)."""
    service = FakePushService()
    sender = sender_with(service)
    sender.send(Phone(GOOGLE).device, MESSAGE)
    sender.send(Phone(APPLE).device, MESSAGE)
    audiences = []
    for request, _timeout in service.received:
        token = header(request, "Authorization").removeprefix("vapid t=").split(",k=")[0]
        audiences.append(jwt.decode(token, options={"verify_signature": False})["aud"])
    assert audiences == ["https://fcm.googleapis.com", "https://web.push.apple.com"]


# ------------------------------------------------------------------ what each answer means


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, PushStatus.DELIVERED),
        (201, PushStatus.DELIVERED),
        (202, PushStatus.DELIVERED),
        (404, PushStatus.GONE),
        (410, PushStatus.GONE),
        (400, PushStatus.REFUSED),
        (401, PushStatus.REFUSED),
        (403, PushStatus.REFUSED),
        (413, PushStatus.REFUSED),
        (429, PushStatus.FAILED),
        (500, PushStatus.FAILED),
        (503, PushStatus.FAILED),
    ],
)
def test_what_a_push_service_answers_decides_what_happens_to_the_device(
    status: int, expected: PushStatus
) -> None:
    result = sender_with(FakePushService(status=status)).send(Phone().device, MESSAGE)
    assert result.status is expected
    assert result.host == "web.push.apple.com"
    assert result.http_status == (None if expected is PushStatus.DELIVERED else status)


def test_a_refusal_keeps_the_push_services_short_reason_and_nothing_else() -> None:
    body = json.dumps({"reason": "BadJwtToken"}).encode()
    result = sender_with(FakePushService(status=403, body=body)).send(Phone().device, MESSAGE)
    assert result.reason == "BadJwtToken"
    assert result.describe() == "web.push.apple.com refused HTTP 403 BadJwtToken"
    # Free text, markup or a long body is dropped: it goes to a log and to the phone.
    for hostile in (b"<html>private</html>", json.dumps({"reason": "x" * 400}).encode(), b"[]"):
        got = sender_with(FakePushService(status=403, body=hostile)).send(Phone().device, MESSAGE)
        assert got.reason is None


def test_the_endpoint_never_appears_in_a_result() -> None:
    """An endpoint is the device's address: whoever has it and our key can notify it."""
    phone = Phone()
    result = sender_with(FakePushService(status=500)).send(phone.device, MESSAGE)
    assert phone.device.endpoint not in result.describe()
    assert "AbCd1234" not in repr(result)


def test_a_connection_failure_is_a_failed_send_not_a_crash() -> None:
    service = FakePushService(error=requests.ConnectionError("no route to host"))
    result = sender_with(service).send(Phone().device, MESSAGE)
    assert result.status is PushStatus.FAILED
    assert result.http_status is None
    assert result.reason == "ConnectionError"  # the kind, never the exception's text
    timed_out = sender_with(FakePushService(error=requests.Timeout())).send(Phone().device, MESSAGE)
    assert timed_out.status is PushStatus.FAILED


def test_keys_a_browser_could_never_have_made_mean_a_lost_device_without_a_send() -> None:
    service = FakePushService()
    sender = sender_with(service)
    good = Phone().device
    for bad in (
        good.model_copy(update={"p256dh": "not-a-key"}),
        good.model_copy(update={"p256dh": b64(b"\x04" + b"\x00" * 10)}),
        good.model_copy(update={"auth": b64(b"short")}),
        good.model_copy(update={"auth": "***"}),
    ):
        result = sender.send(bad, MESSAGE)
        assert result.status is PushStatus.GONE
    assert service.received == []  # nothing went on the wire


# ---------------------------------------------------------- only a known push service is sent to


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://web.push.apple.com/QGuQyavXutnMH9nfu0ApRNCUDhLbR0GVz8Jg6jXzxsqVpWsV",
        "https://fcm.googleapis.com/fcm/send/dKSkXxFtA7A:APA91bH",
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABl",
        "https://wns2-bl2p.notify.windows.com/w/?token=BQYAAAB",
    ],
)
def test_the_four_push_services_are_known(endpoint: str) -> None:
    assert is_push_service(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "https://push.example.test/x",  # any other server
        "http://web.push.apple.com/x",  # not https
        "https://web.push.apple.com.attacker.example/x",  # a known host as a prefix
        "https://attacker.example/web.push.apple.com",  # a known host in the path
        "https://attacker.example/?fcm.googleapis.com",  # or in the query
        "https://web.push.apple.com@attacker.example/x",  # a user name that looks like the host
        "https://user@web.push.apple.com/x",  # a user name at all
        "https://web.push.apple.com:443/x",  # a port, even the right one
        "https://web.push.apple.com\\@attacker.example/x",  # parsers disagree on a backslash
        "https://web.push.apple.com /x",  # whitespace
        "https://web.push.apple.com\t/x",  # a tab, which some parsers drop
        "https://web.push.\u0430pple.com/x",  # a Cyrillic a
        "https://WEB.PUSH.APPLE.COM/x",  # browsers send lowercase; anything else is suspect
        "https://notify.windows.com/x",  # the suffix alone is not a host of the service
        "https://evilnotify.windows.com/x",  # a suffix match must start at a dot
        "https://notify.windows.com.attacker.example/x",
        "https://169.254.169.254/computeMetadata/v1/",  # the cloud's metadata server
        "https://[::1]/x",
        "https://[broken/x",  # one the parser itself refuses
    ],
)
def test_anything_else_is_not(endpoint: str) -> None:
    assert not is_push_service(endpoint)


def test_a_device_on_another_server_is_never_sent_to() -> None:
    """A row saved before the list existed, or written another way, can't make the job send a
    request to some other server: the sender checks the list itself."""
    service = FakePushService()
    result = sender_with(service).send(Phone("https://attacker.example/x").device, MESSAGE)
    assert result.status is PushStatus.BLOCKED
    assert result.host == "attacker.example"
    assert service.received == []  # nothing went on the wire


def test_a_redirect_is_never_followed() -> None:
    """A push service answers where it was asked; following a redirect would send the push, and
    our signed token, somewhere else."""
    service = FakePushService(status=307, headers={"Location": "https://attacker.example/x"})
    result = sender_with(service).send(Phone().device, MESSAGE)
    assert result.status is PushStatus.FAILED
    assert len(service.received) == 1  # the first request only
    assert service.received[0][0].url == APPLE


def test_a_host_in_a_log_line_is_a_plain_host_name_or_unknown() -> None:
    assert endpoint_host(APPLE) == "web.push.apple.com"
    assert endpoint_host("https://web.push.apple.com/secret-path") == "web.push.apple.com"
    for odd in (
        "not a url",
        "https://[broken/x",
        "https://ex ample.com/x",
        "https://a_b.example/x",
    ):
        assert endpoint_host(odd) == "unknown"


# ------------------------------------------------------------------------------ the setting


def settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_the_sender_needs_the_key_and_a_real_https_address_for_the_app() -> None:
    keys = generate_vapid_keys()
    ok = settings(FH_VAPID_PRIVATE_KEY=keys.private_key, FH_APP_ORIGINS=SUBJECT + ",https://other")
    assert isinstance(sender_from_settings(ok), WebPushSender)

    with pytest.raises(PushNotConfiguredError, match="FH_VAPID_PRIVATE_KEY isn't set"):
        sender_from_settings(settings(FH_APP_ORIGINS=SUBJECT))
    for origins in ("", "https://not-set.invalid", "http://app.example.test", "app.example.test"):
        with pytest.raises(PushNotConfiguredError, match="FH_APP_ORIGINS"):
            sender_from_settings(
                settings(FH_VAPID_PRIVATE_KEY=keys.private_key, FH_APP_ORIGINS=origins)
            )


def test_a_private_key_the_library_cant_read_is_named_without_printing_it() -> None:
    bad = "this-is-not-a-key-" + "x" * 30
    with pytest.raises(PushNotConfiguredError) as excinfo:
        sender_from_settings(settings(FH_VAPID_PRIVATE_KEY=bad, FH_APP_ORIGINS=SUBJECT))
    assert "FH_VAPID_PRIVATE_KEY" in str(excinfo.value)
    assert bad not in str(excinfo.value)


def test_the_subject_is_the_first_address_in_the_apps_origins() -> None:
    assert (
        app_address(settings(FH_APP_ORIGINS=f"{SUBJECT}/, https://second.example.test")) == SUBJECT
    )
    assert app_address(settings(FH_APP_ORIGINS="https://second.example.test," + SUBJECT)) == (
        "https://second.example.test"
    )
    assert app_address(settings()) is None
    assert app_address(settings(FH_APP_ORIGINS="https://not-set.invalid")) is None


# ------------------------------------------------------------------------------ the messages


def test_a_report_message_is_counts_a_path_and_a_tag_and_nothing_about_a_league() -> None:
    daily = report_ready(ReportKind.DAILY, date(2026, 11, 20), reports=1, cards=8)
    assert (daily.title, daily.body, daily.path) == (
        "Today's report is ready",
        "8 cards in 1 league.",
        "/today",
    )
    assert daily.tag == "daily-2026-11-20"
    weekly = report_ready(ReportKind.WEEKLY, date(2026, 11, 23), reports=3, cards=1)
    assert (weekly.title, weekly.body, weekly.path) == (
        "This week's overview is ready",
        "1 card in 3 leagues.",
        "/week",
    )
    assert weekly.tag == "weekly-2026-11-23"
    with pytest.raises(ValueError, match="scorecard"):
        report_ready(ReportKind.SCORECARD, date(2026, 11, 20), reports=1, cards=1)


def test_a_payload_is_compact_json_well_under_the_limit() -> None:
    text = report_ready(ReportKind.DAILY, date(2026, 11, 20), reports=12, cards=240).payload()
    assert json.loads(text)["tag"] == "daily-2026-11-20"
    assert " " not in text.split('"title":')[0]  # compact separators
    assert len(text.encode()) < 300 < MAX_PAYLOAD_BYTES


def test_a_message_can_only_send_a_tap_to_one_of_the_apps_tabs() -> None:
    with pytest.raises(ValidationError):
        PushMessage(title="t", body="b", path="/anything", tag="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PushMessage(title="t", body="b", path="/today", tag="Has Spaces")
    with pytest.raises(ValidationError):
        PushMessage(title="", body="b", path="/today", tag="x")


def test_the_test_message_has_its_own_tag() -> None:
    """So a test never replaces a real report's notification on the screen."""
    assert diagnostic_message().tag == "test"
    assert (
        diagnostic_message().tag
        != report_ready(ReportKind.DAILY, date(2026, 11, 20), reports=1, cards=1).tag
    )
