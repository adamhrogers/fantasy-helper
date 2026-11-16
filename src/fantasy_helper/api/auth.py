"""Who is calling: a Supabase access token, checked here against Supabase's public keys (ADR 0029).

The API fetches the project's public signing keys once and keeps them (PyJWT's JWKS client caches
them for ten minutes, which is as long as Supabase's own edge does), then checks every request's
signature, issuer, audience and expiry itself. No shared secret is held, so nothing here could
sign a token, and no request waits on a call to Supabase.

Only the algorithms Supabase's public-key signing uses are accepted. A token that names `HS256`
or `none` is refused before any key is looked at: that is the classic way to forge a token
against a verifier that trusts the header.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt

ALGORITHMS = ("ES256", "RS256")
AUDIENCE = "authenticated"
KEY_LIFETIME_SECONDS = 600


class AuthError(Exception):
    """The token isn't a valid sign-in. The message is safe to log, never to echo to a caller."""


@dataclass(frozen=True)
class Claims:
    subject: UUID  # Supabase Auth's id for the person
    email: str | None


class TokenVerifier:
    def __init__(self, *, issuer: str, key_for: Callable[[str], Any]) -> None:
        self._issuer = issuer
        self._key_for = key_for  # token -> the public key that should have signed it

    def verify(self, token: str) -> Claims:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm not in ALGORITHMS:
                raise AuthError(f"algorithm {algorithm!r} is not accepted")
            claims = jwt.decode(
                token,
                self._key_for(token),
                algorithms=list(ALGORITHMS),
                audience=AUDIENCE,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            subject = UUID(str(claims["sub"]))
        except AuthError:
            raise
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise AuthError(type(exc).__name__) from exc
        if claims.get("is_anonymous"):
            raise AuthError("anonymous sign-ins are not accepted")
        email = claims.get("email")
        return Claims(
            subject=subject, email=email.strip().lower() if isinstance(email, str) else None
        )


def supabase_verifier(supabase_url: str) -> TokenVerifier:
    """The verifier for a Supabase project: its issuer and its public keys, from its JWKS."""
    base = supabase_url.rstrip("/")
    client = jwt.PyJWKClient(
        f"{base}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
        lifespan=KEY_LIFETIME_SECONDS,
        timeout=5,
    )
    return TokenVerifier(
        issuer=f"{base}/auth/v1",
        key_for=lambda token: client.get_signing_key_from_jwt(token).key,
    )
