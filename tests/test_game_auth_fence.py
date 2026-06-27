"""S9.5 — HTTP boundary hardening for the generic game router:

(A) **Input fence with zero ledger movement.** An out-of-range dice ``target``
    (or a bad ``direction``) is rejected with ``422`` BEFORE any debit — the
    wallet reconciles unchanged (no ``WAGER`` booked).
(B) **Server-authoritative identity.** ``/games/{id}/bet`` and ``/state`` require
    a bearer token; identity comes ONLY from the token, never the request body /
    query. No token → ``401``; a forged ``userId`` in the body is ignored — the
    bet is attributed to the TOKEN's user.

Requires the migrated DB (see ``tests/conftest.py``):

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.games import _Runtime
from app.db.models import Bet, LedgerEntry
from app.economy import WELCOME_GRANT_MINOR
from app.main import app
from app.wallet import Ledger

DICE = "originals.dice"


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-signing-key-not-for-prod-0123456789abcdef")


@pytest.fixture
def app_runtime(session_factory: async_sessionmaker[AsyncSession]) -> Iterator[None]:
    """Point the app's lazy DB runtime at the TEST's session factory (same idiom as
    test_auth) so each endpoint call stays on its own test's loop/engine."""
    prev = getattr(app.state, "games_runtime", None)
    app.state.games_runtime = _Runtime(session_factory)
    yield
    app.state.games_runtime = prev


async def _guest(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """Sign up a funded guest. Returns (user_id, wallet_id, access_token)."""
    body = (await client.post("/auth/guest")).json()
    return body["userId"], body["walletId"], body["tokens"]["accessToken"]


async def _wager_count(
    session_factory: async_sessionmaker[AsyncSession], wallet_id: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.wallet_id == wallet_id, LedgerEntry.type == "WAGER")
        )
    return int(n or 0)


# --------------------------------------------------------------------------- (A) fence


@pytest.mark.parametrize(
    "bad_input",
    [
        {"target": 99.5, "direction": "UNDER"},  # p = 0.995 > 0.98
        {"target": 50, "direction": "SIDEWAYS"},  # invalid direction
    ],
)
async def test_out_of_range_input_is_422_with_no_ledger_movement(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    app_runtime: None,
    bad_input: dict[str, object],
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, wid, token = await _guest(client)
        resp = await client.post(
            f"/games/{DICE}/bet",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "betId": f"bet-{uuid4().hex}",
                "stakeMinor": 100,
                "currency": "GOLD",
                "input": bad_input,
            },
        )

    # Rejected at the boundary, NOT a 500 and NOT a 402/409 (no money path entered).
    assert resp.status_code == 422, resp.text
    # The fence ran BEFORE the debit: no WAGER booked, balance untouched.
    assert await _wager_count(session_factory, wid) == 0
    assert await ledger.reconcile(wid) == WELCOME_GRANT_MINOR


# --------------------------------------------------------------------------- (B) auth


async def test_bet_without_token_is_401(app_runtime: None) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/games/{DICE}/bet",
            json={
                "betId": f"bet-{uuid4().hex}",
                "stakeMinor": 100,
                "input": {"target": 50, "direction": "UNDER"},
            },
        )
    assert resp.status_code == 401


async def test_bet_attributed_to_token_user_ignoring_body_userid(
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    """Identity is the token's subject — a ``userId`` smuggled in the body is
    ignored, so a user can NEVER act as another."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uid, _, token = await _guest(client)
        bet_id = f"bet-{uuid4().hex}"
        resp = await client.post(
            f"/games/{DICE}/bet",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "betId": bet_id,
                "userId": "someone-else",  # forged — must be ignored
                "stakeMinor": 100,
                "input": {"target": 50, "direction": "UNDER"},
            },
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["userId"] == uid  # the TOKEN's user, not "someone-else"
    async with session_factory() as session:
        bet = await session.get(Bet, bet_id)
    assert bet is not None
    assert bet.user_id == uid


async def test_state_requires_token_and_returns_callers_round(
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # No token → 401.
        unauth = await client.get(f"/games/{DICE}/state")
        assert unauth.status_code == 401

        uid, _, token = await _guest(client)
        auth = {"Authorization": f"Bearer {token}"}
        await client.post(
            f"/games/{DICE}/bet",
            headers=auth,
            json={
                "betId": f"bet-{uuid4().hex}",
                "stakeMinor": 100,
                "input": {"target": 50, "direction": "UNDER"},
            },
        )
        state = await client.get(f"/games/{DICE}/state", headers=auth)

    assert state.status_code == 200, state.text
    assert state.json()["gameId"] == DICE
