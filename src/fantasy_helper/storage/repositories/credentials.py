"""Platform logins, encrypted at rest and bound to their owner (ADR 0007).

Plaintext exists only in memory, as domain objects with SecretStr fields.
"""

import json
import uuid
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.domain.league import Platform
from fantasy_helper.domain.users import EspnCredentials
from fantasy_helper.storage import crypto
from fantasy_helper.storage.tables import PlatformCredentialRow


def _row(session: Session, user_id: UUID, platform: Platform) -> PlatformCredentialRow | None:
    return session.scalar(
        select(PlatformCredentialRow).where(
            PlatformCredentialRow.user_id == user_id,
            PlatformCredentialRow.platform == platform.value,
        )
    )


def has(session: Session, user_id: UUID, platform: Platform) -> bool:
    return _row(session, user_id, platform) is not None


def save_espn(session: Session, user_id: UUID, credentials: EspnCredentials, key: bytes) -> None:
    plaintext = json.dumps(
        {
            "espn_s2": credentials.espn_s2.get_secret_value(),
            "swid": credentials.swid.get_secret_value(),
        }
    ).encode()
    ciphertext = crypto.encrypt(
        plaintext, key=key, aad=crypto.credential_aad(user_id, Platform.ESPN)
    )
    row = _row(session, user_id, Platform.ESPN)
    if row is None:
        session.add(
            PlatformCredentialRow(
                id=uuid.uuid4(),
                user_id=user_id,
                platform=Platform.ESPN.value,
                ciphertext=ciphertext,
            )
        )
    else:
        row.ciphertext = ciphertext
    session.flush()


def load_espn(session: Session, user_id: UUID, key: bytes) -> EspnCredentials | None:
    row = _row(session, user_id, Platform.ESPN)
    if row is None:
        return None
    data = json.loads(
        crypto.decrypt(row.ciphertext, key=key, aad=crypto.credential_aad(user_id, Platform.ESPN))
    )
    return EspnCredentials(espn_s2=SecretStr(data["espn_s2"]), swid=SecretStr(data["swid"]))
