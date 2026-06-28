"""Faucets (economy sources) — each grants exactly once per eligibility window.

Runs against a REAL, alembic-migrated Postgres (the SYSTEM/house wallet, the
counterparty of every grant, is seeded by the S2 migration) AND a live Redis
(window/streak state). Both are probed at fixture setup so an unreachable service
yields a clear SKIP, not a misleading mid-test failure.

The load-bearing properties under test:
- each faucet credits via a balanced ``GRANT`` LedgerEntry (double-entry);
- a re-claim INSIDE the same window is a no-op (``granted=False``) and books no
  second entry — the ledger UNIQUE ``idempotency_key`` is the dedup authority;
- after the window elapses (clock advanced) the faucet grants again;
- the daily faucet's streak increments on consecutive days and resets on a gap;
- the welcome grant is idempotent under re-trigger (single grant for
  ``hash(userId,'welcome')``; the second call replays, balance unchanged).

Time is injected (``now=...``) so windows advance without sleeping; user ids are
fresh uuids so Redis keys and the table-wide ledger UNIQUE keys never collide.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LedgerEntry, User, Wallet
from app.economy import (
    DAILY_BASE_MINOR,
    DAILY_STREAK_STEP_MINOR,
    HOURLY_GRANT_MINOR,
    LEVEL_UP_BASE_MINOR,
    WELCOME_GRANT_MINOR,
    RedisFaucetStore,
    daily_streak_claim,
    daily_streak_key,
    hourly_claim,
    hourly_key,
    level_up_claim,
    level_up_key,
    welcome_grant,
    welcome_grant_key,
)
from app.wallet import Ledger

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

HOUR = 3600.0
DAY = 86400.0


@pytest.fixture
async def store() -> AsyncIterator[RedisFaucetStore]:
    s = RedisFaucetStore.from_url(REDIS_URL)
    try:
        await s.ping()
    except (OSError, RedisError) as exc:
        await s.aclose()
        pytest.skip(f"no Redis reachable at {REDIS_URL} ({type(exc).__name__}); skipped.")
    try:
        yield s
    finally:
        await s.aclose()


@pytest.fixture
async def user_wallet(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    """A fresh user + zero-balance GOLD/PLAY wallet (unique ids → test isolation)."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
    return uid, wid


async def _entry_by_key(
    session_factory: async_sessionmaker[AsyncSession], key: str
) -> LedgerEntry | None:
    async with session_factory() as session:
        return await session.scalar(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == key)
        )


async def _count_by_key(
    session_factory: async_sessionmaker[AsyncSession], key: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count()).select_from(LedgerEntry).where(
                LedgerEntry.idempotency_key == key
            )
        )
    return int(n or 0)


async def _balance(session_factory: async_sessionmaker[AsyncSession], wallet_id: str) -> int:
    async with session_factory() as session:
        w = await session.get(Wallet, wallet_id)
        assert w is not None
        return w.balance_minor


# --------------------------------------------------------------------------- #
# Hourly timer claim                                                            #
# --------------------------------------------------------------------------- #
async def test_hourly_grants_once_per_window_then_again_after_it(
    ledger: Ledger,
    store: RedisFaucetStore,
    session_factory: async_sessionmaker[AsyncSession],
    user_wallet: tuple[str, str],
) -> None:
    uid, wid = user_wallet
    t0 = 1_000_000.0  # arbitrary epoch inside hour-bucket floor(t0/3600)

    first = await hourly_claim(ledger, store, user_id=uid, wallet_id=wid, now=t0)
    assert first.granted is True
    assert first.amount_minor == HOURLY_GRANT_MINOR

    # Traces to a balanced GRANT LedgerEntry.
    key0 = hourly_key(uid, t0)
    entry = await _entry_by_key(session_factory, key0)
    assert entry is not None and entry.type == "GRANT"
    assert entry.delta_minor == HOURLY_GRANT_MINOR

    # Re-claim INSIDE the same hour → no-op, no second entry.
    again = await hourly_claim(ledger, store, user_id=uid, wallet_id=wid, now=t0 + 60)
    assert again.granted is False
    assert await _count_by_key(session_factory, key0) == 1

    # After the hour elapses → grants again (new bucket, new key, new entry).
    third = await hourly_claim(ledger, store, user_id=uid, wallet_id=wid, now=t0 + HOUR)
    assert third.granted is True
    assert hourly_key(uid, t0 + HOUR) != key0
    assert await _balance(session_factory, wid) == 2 * HOURLY_GRANT_MINOR


# --------------------------------------------------------------------------- #
# Daily streak bonus                                                            #
# --------------------------------------------------------------------------- #
async def test_daily_streak_increments_on_consecutive_days_resets_on_gap(
    ledger: Ledger,
    store: RedisFaucetStore,
    session_factory: async_sessionmaker[AsyncSession],
    user_wallet: tuple[str, str],
) -> None:
    uid, wid = user_wallet
    d0 = 5_000_000.0  # somewhere mid-day-bucket

    day1 = await daily_streak_claim(ledger, store, user_id=uid, wallet_id=wid, now=d0)
    assert day1.granted is True and day1.streak == 1
    assert day1.amount_minor == DAILY_BASE_MINOR
    entry = await _entry_by_key(session_factory, daily_streak_key(uid, d0))
    assert entry is not None and entry.type == "GRANT"

    # Same day → no-op.
    same = await daily_streak_claim(ledger, store, user_id=uid, wallet_id=wid, now=d0 + 100)
    assert same.granted is False
    assert await _count_by_key(session_factory, daily_streak_key(uid, d0)) == 1

    # Next consecutive day → streak 2, larger bonus.
    day2 = await daily_streak_claim(ledger, store, user_id=uid, wallet_id=wid, now=d0 + DAY)
    assert day2.granted is True and day2.streak == 2
    assert day2.amount_minor == DAILY_BASE_MINOR + DAILY_STREAK_STEP_MINOR

    # Skip a day (gap) → streak resets to 1.
    day_after_gap = await daily_streak_claim(
        ledger, store, user_id=uid, wallet_id=wid, now=d0 + 3 * DAY
    )
    assert day_after_gap.granted is True and day_after_gap.streak == 1
    assert day_after_gap.amount_minor == DAILY_BASE_MINOR


# --------------------------------------------------------------------------- #
# Level-up reward                                                               #
# --------------------------------------------------------------------------- #
async def test_level_up_grants_once_per_level(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
    user_wallet: tuple[str, str],
) -> None:
    uid, wid = user_wallet

    lvl2 = await level_up_claim(ledger, user_id=uid, wallet_id=wid, level=2)
    assert lvl2.granted is True
    assert lvl2.amount_minor == LEVEL_UP_BASE_MINOR * 2
    entry = await _entry_by_key(session_factory, level_up_key(uid, 2))
    assert entry is not None and entry.type == "GRANT"

    # Re-claim the SAME level → no-op, single entry.
    again = await level_up_claim(ledger, user_id=uid, wallet_id=wid, level=2)
    assert again.granted is False
    assert await _count_by_key(session_factory, level_up_key(uid, 2)) == 1

    # A new level grants again.
    lvl3 = await level_up_claim(ledger, user_id=uid, wallet_id=wid, level=3)
    assert lvl3.granted is True
    assert lvl3.amount_minor == LEVEL_UP_BASE_MINOR * 3


# --------------------------------------------------------------------------- #
# Welcome grant — single owner, idempotent under re-trigger                     #
# --------------------------------------------------------------------------- #
async def test_welcome_grant_idempotent_under_retrigger(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
    user_wallet: tuple[str, str],
) -> None:
    uid, wid = user_wallet

    first = await welcome_grant(ledger, user_id=uid, wallet_id=wid)
    assert first.replayed is False
    assert first.balance_after == WELCOME_GRANT_MINOR

    second = await welcome_grant(ledger, user_id=uid, wallet_id=wid)
    assert second.replayed is True
    assert second.balance_after == WELCOME_GRANT_MINOR  # balance unchanged

    # Exactly ONE grant for hash(userId,'welcome').
    assert await _count_by_key(session_factory, welcome_grant_key(uid)) == 1
    assert await _balance(session_factory, wid) == WELCOME_GRANT_MINOR
