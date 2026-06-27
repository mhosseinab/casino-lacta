"""``/auth`` — guest sessions, token refresh, and the current-user probe.

Transport-only: it shapes requests/responses and delegates. ``POST /auth/guest``
creates a funded guest (via the service + the welcome-grant seam) and returns a
token pair; ``POST /auth/refresh`` exchanges a refresh token for a fresh access
token; ``GET /auth/me`` is a protected route returning the server-held wallet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.api.games import _runtime
from app.auth.deps import CurrentUser, get_current_user
from app.auth.service import GUEST_CURRENCY, GUEST_MODE, create_guest
from app.auth.tokens import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class _Camel(BaseModel):
    """camelCase JSON over snake_case fields — the project's wire idiom."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class TokenPair(_Camel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class GuestSessionResponse(_Camel):
    user_id: str
    wallet_id: str
    currency: str
    mode: str
    tokens: TokenPair


class RefreshRequest(_Camel):
    refresh_token: str


class AccessToken(_Camel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(_Camel):
    user_id: str
    wallet_id: str
    currency: str
    mode: str
    balance_minor: int


@router.post("/guest", response_model=GuestSessionResponse)
async def post_guest(request: Request) -> GuestSessionResponse:
    rt = _runtime(request)
    guest = await create_guest(rt.session_factory, rt.ledger)
    return GuestSessionResponse(
        user_id=guest.user_id,
        wallet_id=guest.wallet_id,
        currency=GUEST_CURRENCY,
        mode=GUEST_MODE,
        tokens=TokenPair(
            access_token=create_access_token(guest.user_id),
            refresh_token=create_refresh_token(guest.user_id),
        ),
    )


@router.post("/refresh", response_model=AccessToken)
async def post_refresh(body: RefreshRequest) -> AccessToken:
    try:
        user_id = decode_token(body.refresh_token, expected_type="refresh")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return AccessToken(access_token=create_access_token(user_id))


@router.get("/me", response_model=MeResponse)
async def get_me(current: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        user_id=current.user_id,
        wallet_id=current.wallet_id,
        currency=current.currency,
        mode=current.mode,
        balance_minor=current.balance_minor,
    )
