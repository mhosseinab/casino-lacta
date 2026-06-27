"""S6 — the shared bet loop, end to end against a real migrated Postgres.

Binds the iron rules of money movement through the loop:
- SERVER-AUTHORITATIVE: the outcome is derived server-side from
  ``(serverSeed, clientSeed, nonce)``; the caller sends only intent + stake.
- SINGLE-DEBIT / SINGLE-CREDIT: a settled win books exactly ONE ``WAGER`` and
  ONE ``WIN`` row for the wallet — never doubled, even on a resend.
- IDEMPOTENT: a resend of the same ``betId`` returns the ORIGINAL bet and moves
  no further credits.
- AUDIT: one ``AuditEvent`` per bet carries ``configVersion`` read from the DB
  ``GameConfig`` (not an engine literal), plus seed + nonce + input.
- LIMITS / GUARD: an out-of-range stake is rejected with no ledger movement; the
  one-active-round guard blocks a second stateful round for a ``(user, game)``.

Requires the migrated DB (see ``tests/conftest.py``):

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AuditEvent,
    Bet,
    ClientSeed,
    GameConfig,
    GameRound,
    LedgerEntry,
    NonceCounter,
    ServerSeed,
    User,
    Wallet,
)
from app.games import StakeOutOfRange, assert_no_active_round, place_bet
from app.games.bet_loop import ActiveRoundExists
from app.main import app
from app.wallet import Ledger
from engine.rng import create_rng

GAME_ID = "stub.coinflip"
CLIENT_SEED = "test-client-seed"


def _winning_nonce(server_seed: bytes, client_seed: str, *, want_win: bool) -> int:
    """Smallest nonce whose first draw makes stub.coinflip win/lose (heads = roll < 0.5)."""
    n = 0
    while True:
        roll = create_rng(server_seed, client_seed, n).next()
        if (roll < 0.5) == want_win:
            return n
        n += 1


async def _seed_player(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    want_win: bool = True,
    balance_minor: int = 1_000,
) -> tuple[str, str, int, str]:
    """A funded GOLD/PLAY player with a known server/client seed and the nonce
    counter parked on a winning/losing draw. Returns (user_id, wallet_id, nonce,
    server_seed_id)."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    sid = f"s-{uuid4().hex}"
    server_seed = secrets.token_bytes(32)
    nonce = _winning_nonce(server_seed, CLIENT_SEED, want_win=want_win)
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        await session.flush()  # the User must exist before its FK-dependent rows
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
        await session.flush()  # ServerSeed before the NonceCounter that references it
        session.add(NonceCounter(user_id=uid, server_seed_id=sid, value=nonce))
    await ledger.grant(
        wallet_id=wid, amount_minor=balance_minor, idempotency_key=f"{wid}:seed-grant"
    )
    return uid, wid, nonce, sid


async def _ledger_type_count(
    session_factory: async_sessionmaker[AsyncSession], wallet_id: str, type_: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.wallet_id == wallet_id, LedgerEntry.type == type_)
        )
    return int(n or 0)


async def test_win_debits_and_credits_exactly_once_with_audit(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uid, wid, nonce, sid = await _seed_player(session_factory, ledger, want_win=True)
    bet_id = f"bet-{uuid4().hex}"

    bet = await place_bet(
        session_factory,
        ledger,
        user_id=uid,
        game_id=GAME_ID,
        bet_id=bet_id,
        stake_minor=100,
    )

    # Server-decided outcome: a win pays 100 * 1.98 = 198 (floored), status WON.
    assert bet.status == "WON"
    assert bet.outcome["payoutMinor"] == 198
    assert bet.outcome["multiplier"] == pytest.approx(1.98)
    assert bet.fairness.nonce == nonce

    # Ledger moved EXACTLY once each way: one WAGER (-100), one WIN (+198).
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1
    # 1000 (grant) - 100 (stake) + 198 (win) = 1098, reconciled to the cent.
    assert await ledger.reconcile(wid) == 1_098

    # Exactly one AuditEvent, carrying configVersion read from the DB GameConfig.
    async with session_factory() as session:
        audits = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.bet_id == bet_id)
            )
        ).all()
        db_version = await session.scalar(
            select(GameConfig.version).where(GameConfig.game_id == GAME_ID)
        )
    assert len(audits) == 1
    payload = audits[0].payload or {}
    assert payload["configVersion"] == db_version
    assert payload["serverSeedId"] == sid
    assert payload["nonce"] == nonce


async def test_resend_same_betid_returns_original_no_double_movement(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uid, wid, _, _ = await _seed_player(session_factory, ledger, want_win=True)
    bet_id = f"bet-{uuid4().hex}"

    first = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id, stake_minor=100
    )
    second = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id, stake_minor=100
    )

    assert second.bet_id == first.bet_id
    assert second.status == first.status == "WON"
    assert second.outcome["payoutMinor"] == first.outcome["payoutMinor"]
    # Still exactly one of each — the resend re-applied nothing.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1
    assert await ledger.reconcile(wid) == 1_098


async def test_concurrent_same_betid_settles_once_shared_nonce(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The loop's central concurrency claim: two racing calls with the SAME betId
    book one WAGER + one WIN, share ONE nonce, and both return the winner's bet.

    The loser blocks on the nonce counter's FOR UPDATE until the winner commits,
    then loses the betId PK race; its nonce bump rolls back — no divergence, no gap.
    Discriminating: a per-attempt nonce (no shared pin) would let the two derive
    different outcomes and the committed bet could disagree with the applied credit.
    """
    uid, wid, nonce, _ = await _seed_player(session_factory, ledger, want_win=True)
    bet_id = f"bet-{uuid4().hex}"

    async def attempt() -> tuple[int, int, str]:
        bet = await place_bet(
            session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id, stake_minor=100
        )
        return bet.fairness.nonce, bet.outcome["payoutMinor"], bet.status

    a, b = await asyncio.gather(attempt(), attempt())

    # Both callers observe the SAME (winner's) nonce + payout + status.
    assert a == b == (nonce, 198, "WON")
    # Ledger moved exactly once each way despite the race.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1
    assert await ledger.reconcile(wid) == 1_098


async def test_loss_debits_once_and_never_credits(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uid, wid, _, _ = await _seed_player(session_factory, ledger, want_win=False)
    bet_id = f"bet-{uuid4().hex}"

    bet = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id, stake_minor=100
    )

    assert bet.status == "LOST"
    assert bet.outcome["payoutMinor"] == 0
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 0
    assert await ledger.reconcile(wid) == 900  # 1000 - 100, no credit


async def test_stake_outside_limits_rejected_with_no_ledger_movement(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uid, wid, _, _ = await _seed_player(session_factory, ledger, want_win=True)

    with pytest.raises(StakeOutOfRange):
        await place_bet(
            session_factory,
            ledger,
            user_id=uid,
            game_id=GAME_ID,
            bet_id=f"bet-{uuid4().hex}",
            stake_minor=10_000_000_000,  # above max_bet
        )
    # No WAGER booked — balance untouched at the granted 1000.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 0
    assert await ledger.reconcile(wid) == 1_000


async def test_one_active_round_guard_blocks_second_round(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The shared-loop guard stateful games rely on: an ACTIVE round for
    ``(user, game)`` blocks a second; absent one, it passes."""
    uid, wid, _, sid = await _seed_player(session_factory, ledger, want_win=True)

    # No active round yet → passes.
    await assert_no_active_round(session_factory, uid, "table.blackjack")

    rid = f"r-{uuid4().hex}"
    bet_id = f"bet-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(
            GameRound(id=rid, game_id="table.blackjack", type="SINGLE", status="ACTIVE")
        )
        await session.flush()  # round before its referencing bet
        session.add(
            Bet(
                id=bet_id,
                round_id=rid,
                user_id=uid,
                wallet_id=wid,
                currency="GOLD",
                mode="PLAY",
                stake_minor=100,
                status="ACTIVE",
            )
        )

    with pytest.raises(ActiveRoundExists):
        await assert_no_active_round(session_factory, uid, "table.blackjack")


async def test_bet_endpoint_places_bet_end_to_end(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Proves the generic router wiring: POST /games/{id}/bet settles a bet."""
    uid, wid, _, _ = await _seed_player(session_factory, ledger, want_win=True)
    bet_id = f"bet-{uuid4().hex}"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/games/{GAME_ID}/bet",
            json={
                "betId": bet_id,
                "userId": uid,
                "stakeMinor": 100,
                "currency": "GOLD",
                "input": {"side": "heads"},
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["betId"] == bet_id
    assert body["status"] == "WON"
    assert body["outcome"]["payoutMinor"] == 198
    assert body["idempotencyKeys"]["debit"]
    # Server-authoritative result reached the ledger exactly once each way.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1
