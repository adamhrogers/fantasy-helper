"""The token check: what it accepts, and every way a token can be wrong (ADR 0029)."""

import time
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from fantasy_helper.api.auth import AuthError, TokenVerifier

ISSUER = "https://abc.supabase.co/auth/v1"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def token(
    *,
    key: Any = KEY,
    alg: str = "ES256",
    sub: str | None = None,
    exp: float | None = None,
    **claims: Any,
) -> str:
    body: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": sub or str(uuid4()),
        "exp": exp or time.time() + 3600,
        "email": "Adam@Example.com",
    } | claims
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg)


def verifier() -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, key_for=lambda _token: KEY.public_key())


def test_a_good_token_gives_the_person_and_a_lowercased_email() -> None:
    sub = str(uuid4())
    claims = verifier().verify(token(sub=sub))
    assert str(claims.subject) == sub
    assert claims.email == "adam@example.com"


@pytest.mark.parametrize(
    ("what", "bad"),
    [
        ("expired", lambda: token(exp=time.time() - 10)),
        ("wrong audience", lambda: token(aud="anon")),
        ("wrong issuer", lambda: token(iss="https://evil.example/auth/v1")),
        ("signed by another key", lambda: token(key=OTHER_KEY)),
        ("no subject", lambda: token(sub="not-a-uuid")),
        ("anonymous", lambda: token(is_anonymous=True)),
        ("not a token", lambda: "nonsense"),
    ],
)
def test_a_wrong_token_is_refused(what: str, bad: Any) -> None:
    with pytest.raises(AuthError):
        verifier().verify(bad())


def test_a_token_naming_a_shared_secret_algorithm_is_refused_before_any_key_is_used() -> None:
    called: list[str] = []

    def key_for(t: str) -> Any:
        called.append(t)
        return KEY.public_key()

    v = TokenVerifier(issuer=ISSUER, key_for=key_for)
    forged = jwt.encode(
        {"iss": ISSUER, "aud": "authenticated", "sub": str(uuid4()), "exp": time.time() + 60},
        "a-shared-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="not accepted"):
        v.verify(forged)
    assert called == []

    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": "authenticated", "sub": str(uuid4()), "exp": time.time() + 60},
        "",
        algorithm="none",
    )
    with pytest.raises(AuthError):
        v.verify(unsigned)
    assert called == []


def test_a_token_with_no_expiry_is_refused() -> None:
    no_exp = jwt.encode(
        {"iss": ISSUER, "aud": "authenticated", "sub": str(uuid4())}, KEY, algorithm="ES256"
    )
    with pytest.raises(AuthError):
        verifier().verify(no_exp)
