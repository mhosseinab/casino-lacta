"""S23 — the slots spin endpoint, end to end against a real migrated Postgres.

Proves ``POST /games/slots.machine01/spin`` drives the machine through the SHARED bet
loop (single debit → engine outcome → single credit) and returns the §2.2 bet object
whose ``outcome`` carries the grid + line wins. The machine is config-driven: identity
is the registry id + the DB ``GameConfig.params`` seeded from machine01.json (no
per-machine engine code), so this exercises the real runtime path.

DB-dependent: skipped (not failed) when no Postgres is reachable (see ``conftest.py``);
the CORE RTP + parity gate (``test_slots_machine01_rtp``) stays DB-free.

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.games import _Runtime
from app.auth.tokens import create_access_token
from app.db.models import User, Wallet
from app.main import app
from app.wallet import Ledger

GAME_ID = "slots.machine01"


@pytest.fixture
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-signing-key-not-for-prod-0123456789abcdef")


@pytest.fixture
def app_runtime(session_factory: async_sessionmaker[AsyncSession]) -> Iterator[None]:
    prev = getattr(app.state, "games_runtime", None)
    app.state.games_runtime = _Runtime(session_factory)
    yield
    app.state.games_runtime = prev


async def _funded_player(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> tuple[str, str]:
    """A funded GOLD/PLAY player (seeds/nonce are provisioned lazily by the bet loop)."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        await session.flush()
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
    await ledger.grant(wallet_id=wid, amount_minor=100_000, idempotency_key=f"{wid}:seed-grant")
    return uid, wid


async def test_spin_endpoint_returns_grid_and_wins(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
    jwt_secret: None,
) -> None:
    """POST /games/slots.machine01/spin settles a spin and returns grid + line wins."""
    uid, _ = await _funded_player(session_factory, ledger)
    token = create_access_token(uid)
    bet_id = f"spin-{uuid4().hex}"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/games/{GAME_ID}/spin",
            headers={"Authorization": f"Bearer {token}"},
            json={"betId": bet_id, "stakeMinor": 100, "currency": "GOLD"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["betId"] == bet_id
    assert body["gameId"] == GAME_ID
    assert body["status"] in {"WON", "LOST"}

    outcome = body["outcome"]
    grid = outcome["grid"]
    assert len(grid) == 5  # reels
    assert all(len(col) == 3 for col in grid)  # rows
    # The server-decided wins are surfaced (line wins always present; features when triggered).
    assert "lineWins" in outcome
    assert "payoutMinor" in outcome
    # Server-authoritative: a WON spin's payout is floor(stake·multiplier), capped by max_win.
    if body["status"] == "WON":
        assert outcome["payoutMinor"] > 0
        assert outcome["multiplier"] > 0
