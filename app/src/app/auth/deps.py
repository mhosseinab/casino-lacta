"""The current-user FastAPI dependency: resolve the player + their GOLD/PLAY wallet
from a bearer access token.

Server-authoritative: the token carries only the user id (its subject); the wallet
and its balance are read from the DB here, never trusted from the client. A
missing/invalid token yields ``401`` (HTTPBearer runs with ``auto_error=False`` so
this layer raises the 401 itself, rather than FastAPI's default 403).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.api.games import _runtime  # shared lazy DB runtime (same app.state seam)
from app.auth.service import GUEST_CURRENCY, GUEST_MODE
from app.auth.tokens import AuthError, decode_token
from app.db.models import User, Wallet

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated player + their server-held wallet snapshot."""

    user_id: str
    wallet_id: str
    currency: str
    mode: str
    balance_minor: int


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    """Resolve the bearer access token to its player + GOLD/PLAY wallet, or 401."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        user_id = decode_token(credentials.credentials, expected_type="access")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    rt = _runtime(request)
    async with rt.session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="unknown user")
        wallet = await session.scalar(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == GUEST_CURRENCY,
                Wallet.mode == GUEST_MODE,
            )
        )
    if wallet is None:
        raise HTTPException(status_code=404, detail="no GOLD/PLAY wallet for user")
    return CurrentUser(
        user_id=user_id,
        wallet_id=wallet.id,
        currency=wallet.currency,
        mode=wallet.mode,
        balance_minor=wallet.balance_minor,
    )
