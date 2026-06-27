"""S7 — GET /fairness/{betId}: commit-reveal secrecy + verifier parity (DB-backed).

Two load-bearing properties, against a real migrated Postgres (see conftest):

1. **Server-seed secrecy.** While the seed is active (``rotated_at`` NULL) the
   endpoint returns ``serverSeed = null`` and only the commitment hash — never
   leaking an un-revealed seed. After rotation it reveals the exact seed hex.
2. **Verifier parity.** The verifier, fed the SAME persisted
   ``(serverSeed, clientSeed, nonce, input, configVersion)``, reproduces the
   bet's stored outcome bit-for-bit — server == verifier by construction.

Requires the migrated DB:

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.games import _Runtime
from app.db.models import (
    ClientSeed,
    GameConfig,
    NonceCounter,
    ServerSeed,
    User,
    Wallet,
)
from app.games import place_bet
from app.main import app
from app.wallet import Ledger
from engine.types import GameConfig as EngineConfig
from verifier import reproduce

GAME_ID = "stub.coinflip"
CLIENT_SEED = "fairness-client-seed"


@pytest.fixture
def app_runtime(
    session_factory: async_sessionmaker[AsyncSession],
) -> Iterator[None]:
    """Point the app's lazy DB runtime at the TEST's session factory.

    Without this the endpoint builds (and caches on ``app.state``) its own engine
    bound to the first test's event loop; a later function-scoped test on a fresh
    loop would reuse that closed-loop engine. Injecting the per-test factory keeps
    every endpoint call on its own test's loop and engine."""
    prev = getattr(app.state, "games_runtime", None)
    app.state.games_runtime = _Runtime(session_factory)
    yield
    app.state.games_runtime = prev


async def _seed_player(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
) -> tuple[str, str, bytes, str, int]:
    """A funded player with a known server seed parked on nonce 0.

    Returns (user_id, server_seed_id, server_seed_bytes, client_seed, nonce)."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    sid = f"s-{uuid4().hex}"
    server_seed = secrets.token_bytes(32)
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        await session.flush()
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
        session.add(
            ServerSeed(
                id=sid,
                user_id=uid,
                seed_hash=hashlib.sha256(server_seed).hexdigest(),
                seed_encrypted=server_seed.hex(),
            )
        )
        session.add(ClientSeed(user_id=uid, value=CLIENT_SEED))
        await session.flush()
        session.add(NonceCounter(user_id=uid, server_seed_id=sid, value=0))
    await ledger.grant(wallet_id=wid, amount_minor=1_000, idempotency_key=f"{wid}:grant")
    return uid, sid, server_seed, CLIENT_SEED, 0


async def test_fairness_hides_seed_until_rotation_then_reveals(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    uid, sid, server_seed, _, _ = await _seed_player(session_factory, ledger)
    bet_id = f"bet-{uuid4().hex}"
    bet = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id,
        stake_minor=100, input={"side": "heads"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Active seed → serverSeed MUST be withheld (only the commitment shown).
        before = (await client.get(f"/fairness/{bet_id}")).json()
        assert before["serverSeed"] is None
        assert before["revealed"] is False
        assert before["serverSeedHash"] == bet.fairness.server_seed_hash
        assert before["clientSeed"] == CLIENT_SEED
        assert before["nonce"] == bet.fairness.nonce
        assert before["derivation"]
        assert "serverSeedHash=" in before["verifierUrl"]
        # Whole-URL guard: the raw seed must not appear anywhere in the link
        # ("serverSeed=" is not a substring of "serverSeedHash=", so this is exact).
        assert "serverSeed=" not in before["verifierUrl"]

        # Rotate the seed → the raw seed is now revealed for independent audit.
        async with session_factory() as session, session.begin():
            seed = await session.get(ServerSeed, sid)
            assert seed is not None
            seed.rotated_at = datetime.now(UTC)

        after = (await client.get(f"/fairness/{bet_id}")).json()
    assert after["revealed"] is True
    assert after["serverSeed"] == server_seed.hex()


async def test_fairness_verifier_reproduces_persisted_outcome(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    """The strongest parity claim: the verifier, given ONLY the revealed
    fairness fields + the audited config, recomputes the stored outcome exactly."""
    uid, sid, _, _, _ = await _seed_player(session_factory, ledger)
    bet_id = f"bet-{uuid4().hex}"
    bet = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id,
        stake_minor=100, input={"side": "heads"},
    )

    # Reveal the seed (post-rotation), then read it back through the endpoint.
    async with session_factory() as session, session.begin():
        seed = await session.get(ServerSeed, sid)
        assert seed is not None
        seed.rotated_at = datetime.now(UTC)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        disclosed = (await client.get(f"/fairness/{bet_id}")).json()

    # Pull the AUDITED config version this bet used, so the verifier reproduces
    # against the exact edge/params (not just the current registry default).
    async with session_factory() as session:
        cfg_row = await session.scalar(
            select(GameConfig).where(GameConfig.game_id == GAME_ID)
        )
    assert cfg_row is not None
    cfg = EngineConfig(edge=float(cfg_row.edge), params=dict(cfg_row.params or {}))

    outcome = reproduce(
        server_seed=bytes.fromhex(disclosed["serverSeed"]),
        client_seed=disclosed["clientSeed"],
        nonce=disclosed["nonce"],
        game_id=disclosed["gameId"],
        input={"side": "heads"},
        cfg=cfg,
    )

    # The verifier's recomputation == the server's persisted outcome.
    assert outcome.multiplier == bet.outcome["multiplier"]
    assert outcome.detail["won"] == bet.outcome["won"]
    assert outcome.detail["landed"] == bet.outcome["landed"]


async def test_fairness_unknown_bet_404(
    session_factory: async_sessionmaker[AsyncSession],
    app_runtime: None,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/fairness/missing-{uuid4().hex}")
    assert response.status_code == 404
