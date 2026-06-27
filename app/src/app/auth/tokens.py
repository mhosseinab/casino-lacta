"""JWT access/refresh tokens (HS256). Play-money, identifier-only.

The signing key is read from the ``JWT_SECRET`` environment variable AT CALL TIME
and the module fails closed if it is absent — no secret is ever baked into source
(CLAUDE.md: "No secrets in source or logs"). Reading lazily (not at import) also
keeps importing the app DB/secret-free, so the health probe and unit imports do
not require a configured key.

Two token types share one HS256 secret but are distinguished by a ``type`` claim:
an access token authorises requests (short-lived); a refresh token mints new access
tokens (long-lived) and is rejected anywhere an access token is expected, and vice
versa.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

_ALGORITHM = "HS256"
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)

TokenType = Literal["access", "refresh"]


class AuthError(Exception):
    """A token is missing/invalid/expired/of the wrong type, or no signing key is
    configured. The transport layer maps this to ``401``."""


def _signing_key() -> str:
    """The HS256 secret, read lazily from the environment (never a source literal)."""
    key = os.environ.get("JWT_SECRET")
    if not key:
        raise AuthError("JWT_SECRET is not configured")
    return key


def _create(user_id: str, token_type: TokenType, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _signing_key(), algorithm=_ALGORITHM)


def create_access_token(user_id: str) -> str:
    """A short-lived token that authorises requests for ``user_id``."""
    return _create(user_id, "access", ACCESS_TTL)


def create_refresh_token(user_id: str) -> str:
    """A long-lived token whose ONLY power is minting new access tokens."""
    return _create(user_id, "refresh", REFRESH_TTL)


def decode_token(token: str, *, expected_type: TokenType) -> str:
    """Verify ``token``'s signature, expiry, and ``type`` claim; return its subject.

    Raises :class:`AuthError` on any failure (bad signature, expired, wrong type,
    missing subject) — the caller never sees a partially-trusted token.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token, _signing_key(), algorithms=[_ALGORITHM]
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc
    if payload.get("type") != expected_type:
        raise AuthError(f"expected a {expected_type} token, got {payload.get('type')!r}")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("token has no subject")
    return sub
