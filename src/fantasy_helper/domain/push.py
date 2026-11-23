"""Web Push: a device that can be notified, and the message sent to it (ADR 0030).

A message carries counts and a path, never a card's text, a player or a league's name: it passes
through Apple's, Google's or Mozilla's push service on its way to the phone.
"""

import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from fantasy_helper.domain._base import DomainModel

# The most a push can carry is 4,096 bytes (Apple's documented limit; the other services allow
# more). A message here is a few dozen bytes, so this is a guard against a future change, not a
# limit anything reaches.
MAX_PAYLOAD_BYTES = 4096

# Where a tap lands: the app's two tabs, and nothing else. The service worker allows the same two.
PushPath = Literal["/today", "/week"]


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
