"""S8 — play-money auth sessions + the single-owner welcome-grant seam (DB-backed).

Against a real migrated Postgres (see conftest), this proves:

1. **Guest signup funds a wallet from ONE idempotent ledger grant.** A new guest
   gets a ``User`` + GOLD/PLAY ``Wallet`` whose balance traces to exactly one
   ``GRANT`` :class:`LedgerEntry` keyed ``hash(userId,'welcome')`` — never an
   ad-hoc credit, never mutated directly.
2. **The welcome grant is single-owner + idempotent.** Re-triggering it for the
   same user books nothing (the ledger's UNIQUE idempotency fence is the
   authority) — no double-grant.
3. **The current-user dependency is server-authoritative.** A protected route is
   401 without a valid bearer token, 200 with one, and reports the wallet the
   SERVER holds (not anything the client claimed).
4. **Access/refresh are distinct.** A refresh token mints a fresh access token; an
   access token presented to ``/auth/refresh`` is rejected (type mismatch).

Requires the migrated DB (the welcome grant's SYSTEM/house counterparty is seeded
by the S2 migration):

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.games import _Runtime
from app.auth.service import create_guest
from app.db.models import LedgerEntry, Wallet
from app.economy import WELCOME_GRANT_MINOR, welcome_grant, welcome_grant_key
from app.main import app
from app.wallet import Ledger


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a throwaway signing key — the app never bakes a secret into source;
    it reads ``JWT_SECRET`` from the environment at call time."""
    monkeypatch.setenv("JWT_SECRET", "test-signing-key-not-for-prod-0123456789abcdef")


@pytest.fixture
def app_runtime(
    session_factory: async_sessionmaker[AsyncSession],
) -> Iterator[None]:
    """Point the app's lazy DB runtime at the TEST's session factory (same idiom as
    test_fairness_api) so each endpoint call stays on its own test's loop/engine."""
    prev = getattr(app.state, "games_runtime", None)
    app.state.games_runtime = _Runtime(session_factory)
    yield
    app.state.games_runtime = prev


async def test_guest_signup_creates_funded_wallet_from_single_grant(
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/auth/guest")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    uid = body["userId"]
    wid = body["walletId"]
    assert body["tokens"]["accessToken"]
    assert body["tokens"]["refreshToken"]

    async with session_factory() as session:
        wallet = await session.get(Wallet, wid)
        assert wallet is not None
        assert wallet.user_id == uid
        assert wallet.currency == "GOLD"
        assert wallet.mode == "PLAY"
        # Balance is the welcome amount, and it traces to EXACTLY ONE GRANT entry
        # keyed hash(userId,'welcome') — not an ad-hoc credit, not a direct mutation.
        assert wallet.balance_minor == WELCOME_GRANT_MINOR
        grants = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.wallet_id == wid, LedgerEntry.type == "GRANT"
                )
            )
        ).all()
    assert len(grants) == 1
    assert grants[0].idempotency_key == welcome_grant_key(uid)
    assert grants[0].delta_minor == WELCOME_GRANT_MINOR


async def test_welcome_grant_idempotent_no_double_grant(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
) -> None:
    guest = await create_guest(session_factory, ledger)
    # Re-trigger the welcome grant for the SAME user — must NOT double-fund.
    second = await welcome_grant(ledger, user_id=guest.user_id, wallet_id=guest.wallet_id)
    assert second.replayed is True

    async with session_factory() as session:
        wallet = await session.get(Wallet, guest.wallet_id)
        count = await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.idempotency_key == welcome_grant_key(guest.user_id))
        )
    assert wallet is not None
    assert wallet.balance_minor == WELCOME_GRANT_MINOR
    assert count == 1
    # The projection reconciles to the single grant.
    assert await ledger.reconcile(guest.wallet_id) == WELCOME_GRANT_MINOR


async def test_me_requires_token_then_returns_server_held_wallet(
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # No bearer token → 401 (not FastAPI's default 403).
        unauth = await client.get("/auth/me")
        assert unauth.status_code == 401

        signup = (await client.post("/auth/guest")).json()
        token = signup["tokens"]["accessToken"]
        ok = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200, ok.text
    me = ok.json()
    assert me["userId"] == signup["userId"]
    assert me["walletId"] == signup["walletId"]
    assert me["balanceMinor"] == WELCOME_GRANT_MINOR


async def test_me_rejects_garbage_token(app_runtime: None) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_refresh_mints_access_and_rejects_access_token(
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        signup = (await client.post("/auth/guest")).json()
        access = signup["tokens"]["accessToken"]
        refresh = signup["tokens"]["refreshToken"]

        # A refresh token mints a fresh, working access token.
        refreshed = await client.post("/auth/refresh", json={"refreshToken": refresh})
        assert refreshed.status_code == 200, refreshed.text
        new_access = refreshed.json()["accessToken"]
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me.status_code == 200

        # An ACCESS token presented to /refresh is rejected (type mismatch).
        wrong = await client.post("/auth/refresh", json={"refreshToken": access})
    assert wrong.status_code == 401
