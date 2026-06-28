"""S14 — HiLo server-held-state saga, end to end against a real migrated Postgres.

The second stateful game's money + state contract (it draws a FRESH card per guess,
so it exercises the rng-into-step + {cursor, game} envelope the S12 saga grew):
- SINGLE-DEBIT at round open; SINGLE-CREDIT at cashout (never on a guess).
- The cumulative multiplier compounds across winning guesses; payout is floored ONCE
  at cashout (stake × cumulative), then capped to max_win.
- A wrong guess LOSES the round (no credit).
- The shared one-active-(user,game)-round guard blocks a second concurrent round.
- The saga reconstructs the seeded stream at the persisted cursor before each guess —
  so the card the server draws equals the one a white-box peek (same reconstruction)
  predicts. HiLo holds NO secret state: the whole game state is the safe projection.

Requires the migrated DB (see tests/conftest.py):

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ClientSeed,
    GameRound,
    LedgerEntry,
    NonceCounter,
    ServerSeed,
    User,
    Wallet,
)
from app.games import ActiveRoundExists, place_bet
from app.games.bet_loop import step_action
from app.wallet import Ledger
from engine.games.hilo import draw_rank, step_multiplier
from engine.money import apply_multiplier, cap
from engine.rng import HmacRngStream

GAME_ID = "originals.hilo"
CLIENT_SEED = "hilo-saga-client-seed"
EDGE = 0.01
MAX_WIN = 1_000_000_000  # the seeded GOLD limit (migration d4e5f6a7b8c9)


async def _seed_player(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    balance_minor: int = 100_000,
) -> tuple[str, str]:
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
    await ledger.grant(
        wallet_id=wid, amount_minor=balance_minor, idempotency_key=f"{wid}:seed-grant"
    )
    return uid, wid


async def _peek_next(
    session_factory: async_sessionmaker[AsyncSession], round_id: str, user_id: str
) -> tuple[int, int]:
    """White-box: reconstruct the stream at the stored cursor (exactly as the saga does)
    and return (shown_rank, next_rank). The app never does this — the test does, to drive
    a guaranteed win/loss deterministically (mirrors Mines reading the mine layout)."""
    async with session_factory() as session:
        rr = await session.get(GameRound, round_id)
        ss = await session.get(ServerSeed, rr.server_seed_id)  # type: ignore[union-attr]
        cs = await session.get(ClientSeed, user_id)
    assert rr is not None and ss is not None and cs is not None
    envelope = dict(rr.server_state or {})
    game = dict(envelope.get("game") or {})
    cursor = int(envelope.get("cursor", 0))
    rng = HmacRngStream(bytes.fromhex(ss.seed_encrypted), cs.value, rr.nonce or 0, cursor=cursor)
    return int(game["shown_rank"]), draw_rank(rng)


def _winning_side(shown: int, nxt: int) -> str:
    """A side guaranteed to win: HIGHER wins on next >= shown (tie wins either)."""
    return "HIGHER" if nxt >= shown else "LOWER"


def _losing_side(shown: int, nxt: int) -> str | None:
    """The side that loses, or None on a tie (no side loses a tie)."""
    if nxt > shown:
        return "LOWER"
    if nxt < shown:
        return "HIGHER"
    return None


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


# --- happy path: open → winning guess → cashout ------------------------------


async def test_open_guess_cashout_single_debit_single_credit(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"

    opened = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid, stake_minor=1_000
    )
    assert opened.status == "ACTIVE"
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 0

    shown, nxt = await _peek_next(session_factory, rid, uid)
    side = _winning_side(shown, nxt)
    resp = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "guess", "side": side},
    )
    assert resp["won"] is True
    assert resp["status"] == "ACTIVE"
    assert resp["revealedRank"] == nxt
    assert await _ledger_type_count(session_factory, wid, "WIN") == 0  # no credit on a guess

    expected_mult = step_multiplier(shown, side, EDGE)
    expected_payout = cap(apply_multiplier(1_000, expected_mult), MAX_WIN)

    cash = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "cashout"},
    )
    assert cash["status"] == "CASHED_OUT"
    assert cash["steps"] == 1
    assert cash["payoutMinor"] == expected_payout

    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1
    assert await ledger.reconcile(wid) == 100_000 - 1_000 + expected_payout


async def test_two_winning_guesses_compound(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two winning guesses compound; cashout pays floor(stake × cumulative) once."""
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid, stake_minor=10_000
    )
    cumulative = 1.0
    for _ in range(2):
        shown, nxt = await _peek_next(session_factory, rid, uid)
        side = _winning_side(shown, nxt)
        cumulative *= step_multiplier(shown, side, EDGE)
        resp = await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "guess", "side": side},
        )
        assert resp["won"] is True
        assert resp["currentMultiplier"] == pytest.approx(cumulative, rel=1e-9)

    expected_payout = cap(apply_multiplier(10_000, cumulative), MAX_WIN)
    cash = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "cashout"},
    )
    assert cash["steps"] == 2
    assert cash["payoutMinor"] == expected_payout
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1


# --- losing -----------------------------------------------------------------


async def test_losing_guess_no_credit(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A wrong guess LOSES; no credit lands; balance == start - stake."""
    for _ in range(8):  # retry across fresh rounds until the next card is not a tie
        uid, wid = await _seed_player(session_factory, ledger)
        rid = f"r-{uuid4().hex}"
        await place_bet(
            session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid, stake_minor=1_000
        )
        shown, nxt = await _peek_next(session_factory, rid, uid)
        side = _losing_side(shown, nxt)
        if side is None:  # a tie — no losing side; try another round
            continue
        resp = await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "guess", "side": side},
        )
        assert resp["won"] is False
        assert resp["status"] == "LOST"
        assert await _ledger_type_count(session_factory, wid, "WIN") == 0
        assert await ledger.reconcile(wid) == 100_000 - 1_000
        return
    pytest.skip("no non-tie next card across retries (vanishingly unlikely)")


# --- the one-active-round guard ----------------------------------------------


async def test_guard_blocks_second_concurrent_round(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        bet_id=f"r-{uuid4().hex}", stake_minor=1_000,
    )
    with pytest.raises(ActiveRoundExists):
        await place_bet(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            bet_id=f"r-{uuid4().hex}", stake_minor=1_000,
        )
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1


async def test_open_resend_returns_active_no_second_debit(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    first = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid, stake_minor=1_000
    )
    second = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid, stake_minor=1_000
    )
    assert second.bet_id == first.bet_id
    assert second.status == "ACTIVE"
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
