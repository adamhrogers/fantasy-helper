"""Encryption for platform credentials (ESPN cookies, later Yahoo tokens).

AES-256-GCM with a random nonce per message. Each ciphertext is bound to its owner through
associated data, so a ciphertext copied onto another user's row won't decrypt. The key is a
system secret (FH_ENCRYPTION_KEY) and never lives in the database (docs/architecture.md §12.1).

Blob layout: 1 byte format version | 12 byte nonce | ciphertext + 16 byte tag.
"""

import base64
import binascii
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from fantasy_helper.domain.league import Platform

KEY_BYTES = 32
NONCE_BYTES = 12
FORMAT_VERSION = 1


class CryptoError(Exception):
    """The key is malformed, or a ciphertext can't be decrypted with it."""


def generate_key() -> str:
    """A new random key, encoded for an environment variable."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def load_key(encoded: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise CryptoError("encryption key is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError(f"encryption key must be {KEY_BYTES} bytes, got {len(key)}")
    return key


def credential_aad(user_id: UUID, platform: Platform) -> bytes:
    """Associated data that ties a credential ciphertext to its owner and platform."""
    return f"fh:credential:{user_id}:{platform.value}".encode()


def encrypt(plaintext: bytes, *, key: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return bytes([FORMAT_VERSION]) + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(blob: bytes, *, key: bytes, aad: bytes) -> bytes:
    if len(blob) < 1 + NONCE_BYTES or blob[0] != FORMAT_VERSION:
        raise CryptoError("unrecognized ciphertext format")
    nonce, ciphertext = blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise CryptoError(
            "ciphertext failed authentication (wrong key, owner or tampered)"
        ) from exc
