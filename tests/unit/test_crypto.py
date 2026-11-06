import base64
from uuid import uuid4

import pytest

from fantasy_helper.domain.league import Platform
from fantasy_helper.storage import crypto


@pytest.fixture
def key() -> bytes:
    return crypto.load_key(crypto.generate_key())


def test_round_trip(key: bytes) -> None:
    aad = crypto.credential_aad(uuid4(), Platform.ESPN)
    blob = crypto.encrypt(b'{"espn_s2": "abc"}', key=key, aad=aad)
    assert crypto.decrypt(blob, key=key, aad=aad) == b'{"espn_s2": "abc"}'
    assert b"abc" not in blob


def test_each_encryption_uses_a_fresh_nonce(key: bytes) -> None:
    aad = b"same"
    assert crypto.encrypt(b"x", key=key, aad=aad) != crypto.encrypt(b"x", key=key, aad=aad)


def test_ciphertext_moved_to_another_user_does_not_decrypt(key: bytes) -> None:
    blob = crypto.encrypt(b"cookie", key=key, aad=crypto.credential_aad(uuid4(), Platform.ESPN))
    with pytest.raises(crypto.CryptoError, match="authentication"):
        crypto.decrypt(blob, key=key, aad=crypto.credential_aad(uuid4(), Platform.ESPN))


def test_tampered_ciphertext_is_rejected(key: bytes) -> None:
    blob = bytearray(crypto.encrypt(b"cookie", key=key, aad=b"a"))
    blob[-1] ^= 0x01
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(bytes(blob), key=key, aad=b"a")


def test_wrong_key_is_rejected(key: bytes) -> None:
    blob = crypto.encrypt(b"cookie", key=key, aad=b"a")
    other = crypto.load_key(crypto.generate_key())
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(blob, key=other, aad=b"a")


def test_unknown_format_version_is_rejected(key: bytes) -> None:
    blob = crypto.encrypt(b"cookie", key=key, aad=b"a")
    with pytest.raises(crypto.CryptoError, match="format"):
        crypto.decrypt(b"\x09" + blob[1:], key=key, aad=b"a")


def test_generated_keys_are_32_bytes() -> None:
    assert len(base64.urlsafe_b64decode(crypto.generate_key())) == crypto.KEY_BYTES


@pytest.mark.parametrize("bad", ["not base64!!", base64.urlsafe_b64encode(b"short").decode()])
def test_malformed_keys_are_rejected(bad: str) -> None:
    with pytest.raises(crypto.CryptoError):
        crypto.load_key(bad)
