"""Auth (app member) — play-money guest sessions + JWT access/refresh.

Minimal by design (build-plan: "JWT sessions — play-money; light"). Identifier-only
auth: a guest is a bare ``User`` + their GOLD/PLAY ``Wallet`` — NO payment data, NO
KYC, NO PII beyond the generated id. The JWT signing key is read from the
environment at call time (never baked into source). The current-user dependency
resolves the user + their wallet server-side from the bearer token, so balances are
always server-authoritative.

The welcome grant is NOT issued here — :func:`app.auth.service.create_guest` calls
the single-owner ``app.economy.welcome_grant`` seam (no duplicate grant logic).
"""

from app.auth.deps import CurrentUser, get_current_user
from app.auth.router import router
from app.auth.service import GuestSession, create_guest
from app.auth.tokens import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
)

__all__ = [
    "AuthError",
    "CurrentUser",
    "GuestSession",
    "create_access_token",
    "create_guest",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "router",
]
