"""Web Push: a device that can be notified, and the message sent to it (ADR 0030).

A message carries counts and a path, never a card's text, a player or a league's name: it passes
through Apple's, Google's or Mozilla's push service on its way to the phone.
"""

import json
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field

from fantasy_helper.domain._base import DomainModel

# The most a push can carry is 4,096 bytes (Apple's documented limit; the other services allow
# more). A message here is a few dozen bytes, so this is a guard against a future change, not a
# limit anything reaches.
MAX_PAYLOAD_BYTES = 4096

# Where a tap lands: the app's two tabs, and nothing else. The service worker allows the same two.
PushPath = Literal["/today", "/week"]

# The push services a browser subscribes through: the only hosts a device's address may name. The
# API refuses to save any other address and the sender refuses to send to one, because the address
# comes from the browser, through a signed-in user, and the report jobs would otherwise send a
# request to wherever it points (ADR 0030). Exact hosts, plus one exact suffix for Microsoft's
# service, whose hosts are regional (wns2-<region>.notify.windows.com). A new browser's service is
# added here, in one place: the API and the sender both read this list.
PUSH_SERVICE_HOSTS = frozenset(
    {
        "web.push.apple.com",  # Safari, on the iPhone and the Mac
        "fcm.googleapis.com",  # Chrome, and the other Chromium browsers except Edge
        "updates.push.services.mozilla.com",  # Firefox
    }
)
PUSH_SERVICE_SUFFIXES = (".notify.windows.com",)  # Edge: Windows Push Notification Services

_HOST_NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")


def endpoint_host(endpoint: str) -> str:
    """A device address's host, for a log line or a warning: never the rest of the address (the
    path is the device's own secret), and "unknown" for anything that isn't a plain host name."""
    try:
        host = urlsplit(endpoint).hostname or ""
    except ValueError:
        return "unknown"
    return host if _HOST_NAME.fullmatch(host) else "unknown"


def is_push_service(endpoint: str) -> bool:
    """Whether a device's address is https on one of the push services above, written so that
    every URL parser reads the same host: plain ASCII, no whitespace or backslash, and no user
    name, password or port (a real push address has none of them)."""
    if not endpoint.isascii() or "\\" in endpoint:
        return False
    if any(ch.isspace() or not ch.isprintable() for ch in endpoint):
        return False
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = parts.hostname
    if parts.scheme != "https" or not host or parts.netloc != host:
        return False
    if host in PUSH_SERVICE_HOSTS:
        return True
    return any(
        host.endswith(suffix) and len(host) > len(suffix) for suffix in PUSH_SERVICE_SUFFIXES
    )


class PushDevice(DomainModel):
    """One device's subscription, as the browser gave it to the app."""

    id: UUID
    endpoint: str
    p256dh: str
    auth: str


class PushMessage(DomainModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(max_length=200)
    path: PushPath
    # A notification with the same tag replaces the one already on the screen, so a message sent
    # twice (a failed run retried, a double start) shows once.
    tag: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")

    def payload(self) -> str:
        """What the service worker reads: JSON, compact, under the size limit."""
        text = json.dumps(
            {"title": self.title, "body": self.body, "path": self.path, "tag": self.tag},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if len(text.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"a push payload can't pass {MAX_PAYLOAD_BYTES} bytes")
        return text
