"""S37 — leaderboards (Redis sorted sets), durable snapshots, and the sinks check.

Two concerns, both against the REAL infra (migrated Postgres + live Redis):

1. **Leaderboards.** Three Redis sorted sets — biggest-win, total-wagered, profit —
   updated ON SETTLE; ranking queries (top-N, a user's rank) read them back; a
   point-in-time SNAPSHOT persists durably to Postgres so a Redis flush keeps history.
   Keys are namespaced per ``currency``; each test uses a UNIQUE throwaway currency so
   the shared Redis (``…/0``) needs no ``flushdb`` (which would nuke other worktrees).

2. **Sinks confirmation (read-only).** This step adds NO money movement. It VERIFIES
   that the two play-money economy sinks already land on the SYSTEM/house account via
   the ledger:
   - **house edge** — ``engine.money.apply_multiplier`` floors the payout; the player
     is credited the FLOORED amount, so the truncated fraction is retained house-side.
   - **poker rake** — ``Lobby.take_rake`` debits the pot winner (player → house).
   Asserted on the house-side ledger rows the test itself booked (scoped by ``ref`` /
   the global ``Σ == 0`` invariant), never on the shared house wallet's absolute balance.

Requires the migrated DB + Redis (see ``tests/conftest.py``):

    docker compose up -d postgres redis
    DATABASE_URL=… alembic upgrade head
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ClientSeed,
    LedgerEntry,
    NonceCounter,
    ServerSeed,
    User,
    Wallet,
)
from app.economy.leaderboards import (
    BIGGEST_WIN,
    PROFIT,
    TOTAL_WAGERED,
    Settlement,
    load_snapshot,
    rank_of,
    record_settlement,
    snapshot,
    top,
)
from app.games import place_bet
from app.poker.lobby import DEFAULT_RAKE, Lobby
from app.wallet import Ledger
from engine.money import apply_multiplier, cap
from engine.rng import create_rng

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
HOUSE_WALLET_ID = "house-GOLD"
GAME_ID = "stub.coinflip"
CLIENT_SEED = "test-client-seed"


@pytest.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    """A live Redis client (``decode_responses`` → str members). Skips cleanly when
    Redis is unreachable, mirroring the Postgres fixture's behaviour."""
    client: aioredis.Redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except (RedisError, OSError) as exc:
        await client.aclose()
        pytest.skip(f"no Redis reachable at {REDIS_URL} ({type(exc).__name__}); skipped.")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def ns() -> str:
    """A unique throwaway 'currency' that namespaces this test's Redis keys — isolates
    boards across tests/worktrees on the shared DB without a destructive flush."""
    return f"T{uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# 1. Leaderboards — boards update on settle; rankings read back; snapshot persists
# --------------------------------------------------------------------------- #
async def test_settlement_updates_all_three_boards(
    redis_client: aioredis.Redis, ns: str
) -> None:
    """One settle moves the three boards: wagered += stake, profit += (payout-stake),
    biggest-win = the win."""
    await record_settlement(
        redis_client, Settlement(user_id="u1", stake_minor=100, payout_minor=250), currency=ns
    )

    assert (await rank_of(redis_client, TOTAL_WAGERED, "u1", currency=ns)).score == 100
    assert (await rank_of(redis_client, PROFIT, "u1", currency=ns)).score == 150  # 250-100
    assert (await rank_of(redis_client, BIGGEST_WIN, "u1", currency=ns)).score == 250


async def test_biggest_win_keeps_the_maximum_and_others_accumulate(
    redis_client: aioredis.Redis, ns: str
) -> None:
    """Across two settles for one user: total-wagered & profit ACCUMULATE; biggest-win
    keeps the larger single win (GT semantics), not the latest or the sum."""
    await record_settlement(
        redis_client, Settlement(user_id="u1", stake_minor=100, payout_minor=500), currency=ns
    )
    await record_settlement(
        redis_client, Settlement(user_id="u1", stake_minor=100, payout_minor=0), currency=ns
    )  # a loss

    assert (await rank_of(redis_client, TOTAL_WAGERED, "u1", currency=ns)).score == 200
    assert (await rank_of(redis_client, PROFIT, "u1", currency=ns)).score == 300  # +400 -100
    assert (await rank_of(redis_client, BIGGEST_WIN, "u1", currency=ns)).score == 500  # max


async def test_ranking_top_n_and_user_rank_are_correct(
    redis_client: aioredis.Redis, ns: str
) -> None:
    """top(n) returns the highest-first ordering; rank_of returns a user's 1-based place."""
    for uid, win in (("low", 100), ("high", 900), ("mid", 400)):
        await record_settlement(
            redis_client, Settlement(user_id=uid, stake_minor=10, payout_minor=win), currency=ns
        )

    leaders = await top(redis_client, BIGGEST_WIN, n=2, currency=ns)
    assert [e.user_id for e in leaders] == ["high", "mid"]
    assert [e.rank for e in leaders] == [1, 2]
    assert [e.score for e in leaders] == [900, 400]

    assert (await rank_of(redis_client, BIGGEST_WIN, "high", currency=ns)).rank == 1
    assert (await rank_of(redis_client, BIGGEST_WIN, "low", currency=ns)).rank == 3
    assert await rank_of(redis_client, BIGGEST_WIN, "absent", currency=ns) is None


async def test_snapshot_persists_durably_and_reads_back(
    redis_client: aioredis.Redis,
    ns: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A point-in-time board snapshot is written to Postgres and recoverable verbatim —
    so a Redis flush does not lose the history."""
    for uid, win in (("alice", 700), ("bob", 300)):
        await record_settlement(
            redis_client, Settlement(user_id=uid, stake_minor=50, payout_minor=win), currency=ns
        )

    snap_id = await snapshot(redis_client, session_factory, currency=ns, top_n=10)

    persisted = await load_snapshot(session_factory, snap_id, BIGGEST_WIN, currency=ns)
    assert [(e.user_id, e.score, e.rank) for e in persisted] == [
        ("alice", 700, 1),
        ("bob", 300, 2),
    ]
    # The other boards were captured in the SAME snapshot id.
    wagered = await load_snapshot(session_factory, snap_id, TOTAL_WAGERED, currency=ns)
    assert {e.user_id: e.score for e in wagered} == {"alice": 50, "bob": 50}


# --------------------------------------------------------------------------- #
# 2. Sinks — house edge (floored payout) + poker rake accrue to the house account
# --------------------------------------------------------------------------- #
def _winning_nonce(server_seed: bytes, client_seed: str, *, want_win: bool) -> int:
    """Smallest nonce whose first stub.coinflip draw wins/loses (heads = roll < 0.5)."""
    n = 0
    while (create_rng(server_seed, client_seed, n).next() < 0.5) != want_win:
        n += 1
    return n


async def _seed_player(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    want_win: bool,
    balance_minor: int = 1_000,
) -> tuple[str, str]:
    """A funded GOLD/PLAY player parked on a winning/losing nonce. Returns (uid, wid)."""
    uid, wid, sid = (f"u-{uuid4().hex}", f"w-{uuid4().hex}", f"s-{uuid4().hex}")
    server_seed = secrets.token_bytes(32)
    nonce = _winning_nonce(server_seed, CLIENT_SEED, want_win=want_win)
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
        session.add(NonceCounter(user_id=uid, server_seed_id=sid, value=nonce))
    await ledger.grant(wallet_id=wid, amount_minor=balance_minor, idempotency_key=f"{wid}:seed")
    return uid, wid


async def _house_delta_by_ref(
    session_factory: async_sessionmaker[AsyncSession], ref: str
) -> int:
    """Σ of the house-side ledger deltas booked under one ``ref`` — isolates THIS test's
    sink txns from the shared house wallet's (irrelevant) running balance."""
    async with session_factory() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
                LedgerEntry.wallet_id == HOUSE_WALLET_ID, LedgerEntry.ref == ref
            )
        )
    return int(total or 0)


async def test_floored_payout_is_the_house_margin(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """House edge mechanism: ``apply_multiplier`` FLOORS the payout, so the player is
    credited the floored amount and the truncated fraction never leaves the house.

    stub.coinflip win at 1.98×, stake 10 → fair 19.80 → floored 19. The WIN credited to
    the player (house-side row = -19) is the floored amount, NOT 19.8 — the 0.80 is the
    house's margin, retained house-side by construction."""
    uid, wid = await _seed_player(session_factory, ledger, want_win=True)
    bet_id = f"bet-{uuid4().hex}"

    bet = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id, stake_minor=10
    )

    assert apply_multiplier(10, 1.98) == 19  # the floor (fair 19.8 truncated)
    assert bet.outcome["payoutMinor"] == cap(apply_multiplier(10, 1.98), 1_000_000) == 19
    # The player's WIN was credited the FLOORED 19 — the house paid only the floored
    # amount, so the 0.8 truncation is provably retained house-side.
    async with session_factory() as session:
        win_house_delta = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
                LedgerEntry.wallet_id == HOUSE_WALLET_ID,
                LedgerEntry.ref == bet_id,
                LedgerEntry.type == "WIN",
            )
        )
    assert int(win_house_delta or 0) == -19


async def test_house_edge_and_poker_rake_accrue_to_house(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Σ-to-house: a settled loss (full stake → house) and a poker rake (pot cut → house)
    both land on the SYSTEM/house account, and the system stays conservation-closed."""
    # House edge — a LOST bet: stake 100 moves player → house, no credit back.
    uid, wid = await _seed_player(session_factory, ledger, want_win=False)
    bet_id = f"bet-{uuid4().hex}"
    bet = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=bet_id, stake_minor=100
    )
    assert bet.status == "LOST" and bet.outcome["payoutMinor"] == 0
    assert await _house_delta_by_ref(session_factory, bet_id) == 100  # full stake → house

    # Poker rake — DEFAULT_RAKE (5%, cap 300) on a 1000 pot = 50, debited player → house.
    _, poker_wid = await _seed_player(session_factory, ledger, want_win=True)
    lobby = Lobby(session_factory, ledger)
    table_id = f"t-{uuid4().hex}"
    rake = await lobby.take_rake(
        table_id=table_id, hand_no=1, seat=0, wallet_id=poker_wid, pot_minor=1_000
    )
    assert rake.rake_minor == DEFAULT_RAKE.rake_for(1_000) == 50
    assert rake.ledger is not None
    # Scope to the rake op's own txn_id (decoupled from the lobby's ref string): the
    # house-side row of the balanced pair gained exactly the rake.
    async with session_factory() as session:
        rake_house_delta = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
                LedgerEntry.wallet_id == HOUSE_WALLET_ID,
                LedgerEntry.txn_id == rake.ledger.txn_id,
            )
        )
    assert int(rake_house_delta or 0) == 50  # rake → house

    # Both sinks net to zero system-wide (closed double-entry: every sink has its
    # player-side counterparty). The sinks ACCRUE to house; the system conserves.
    assert await ledger.system_total() == 0
