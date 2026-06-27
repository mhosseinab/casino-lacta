"""Double-entry ledger: idempotency, no-overspend, reversibility, conservation.

Security-grade invariants (CLAUDE.md):
- every op is a balanced two-row set summing to zero (system-wide Σ == 0);
- a wallet's projection always equals Σ(its entries) (reconcile);
- a replayed idempotency key applies exactly once;
- a debit cannot drive the balance negative, even under N-way concurrency.

Idempotency keys are namespaced by the per-test ``wallet_id`` because the UNIQUE
constraint is table-wide and tests share one (un-truncated) database — a bare
``"g"`` would be deduped against another test's grant.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LedgerEntry, User, Wallet
from app.wallet import InsufficientFunds, Ledger


async def _wallet_entry_count(
    session_factory: async_sessionmaker[AsyncSession], wallet_id: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count()).select_from(LedgerEntry).where(
                LedgerEntry.wallet_id == wallet_id
            )
        )
    return int(n or 0)


async def _key_entry_count(
    session_factory: async_sessionmaker[AsyncSession], idempotency_key: str
) -> int:
    """Count player-side rows carrying ``idempotency_key`` — must be 1 if the op
    applied exactly once. This is what the UNIQUE fence makes impossible to exceed."""
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count()).select_from(LedgerEntry).where(
                LedgerEntry.idempotency_key == idempotency_key
            )
        )
    return int(n or 0)


async def _distinct_txn_ids_for_key(
    session_factory: async_sessionmaker[AsyncSession], idempotency_key: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count(func.distinct(LedgerEntry.txn_id))).where(
                LedgerEntry.idempotency_key == idempotency_key
            )
        )
    return int(n or 0)


async def _make_wallet(
    session_factory: async_sessionmaker[AsyncSession], *, balance_minor: int = 0
) -> str:
    """A fresh, isolated GOLD/PLAY wallet (its own user) — for multi-wallet tests."""
    wid = f"w-{uuid4().hex}"
    uid = f"u-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(
            Wallet(
                id=wid,
                user_id=uid,
                currency="GOLD",
                mode="PLAY",
                balance_minor=balance_minor,
            )
        )
    return wid


async def test_grant_then_reconcile_balanced(
    ledger: Ledger,
    wallet_id: str,
) -> None:
    result = await ledger.grant(
        wallet_id=wallet_id, amount_minor=1_000, idempotency_key=f"{wallet_id}:g"
    )
    assert result.balance_after == 1_000
    assert result.delta_minor == 1_000
    assert not result.replayed
    # Projection == Σ entries to the cent.
    assert await ledger.reconcile(wallet_id) == 1_000


async def test_debit_is_idempotent_double_send_applied_once(
    ledger: Ledger,
    wallet_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ledger.grant(wallet_id=wallet_id, amount_minor=1_000, idempotency_key=f"{wallet_id}:g")
    key = f"{wallet_id}:bet:abc:WAGER"

    first = await ledger.debit(wallet_id=wallet_id, amount_minor=250, idempotency_key=key)
    second = await ledger.debit(wallet_id=wallet_id, amount_minor=250, idempotency_key=key)

    assert not first.replayed
    assert second.replayed
    # Same original result, applied ONCE: balance moved 1000 -> 750, not 500.
    assert first.balance_after == 750
    assert second.balance_after == 750
    assert second.txn_id == first.txn_id
    assert await ledger.reconcile(wallet_id) == 750
    # One player row for the grant + one for the single applied debit = 2.
    assert await _wallet_entry_count(session_factory, wallet_id) == 2


async def test_debit_rejects_insufficient_funds_no_negative_balance(
    ledger: Ledger,
    wallet_id: str,
) -> None:
    await ledger.grant(wallet_id=wallet_id, amount_minor=100, idempotency_key=f"{wallet_id}:g")
    with pytest.raises(InsufficientFunds):
        await ledger.debit(
            wallet_id=wallet_id, amount_minor=101, idempotency_key=f"{wallet_id}:overspend"
        )
    # Nothing booked: balance unchanged and still reconciled.
    assert await ledger.reconcile(wallet_id) == 100


async def test_credit_then_rollback_reverses_balanced_pair(
    ledger: Ledger,
    wallet_id: str,
) -> None:
    await ledger.grant(wallet_id=wallet_id, amount_minor=500, idempotency_key=f"{wallet_id}:g")
    win = await ledger.credit(
        wallet_id=wallet_id, amount_minor=300, idempotency_key=f"{wallet_id}:win1"
    )
    assert win.balance_after == 800

    rb = await ledger.rollback(op_ref=win.txn_id)
    assert rb.delta_minor == -300  # inverse of the credit
    assert rb.balance_after == 500
    assert await ledger.reconcile(wallet_id) == 500

    # Rollback is itself idempotent.
    rb2 = await ledger.rollback(op_ref=win.txn_id)
    assert rb2.replayed
    assert await ledger.reconcile(wallet_id) == 500


async def test_n_parallel_debits_cannot_overspend(
    ledger: Ledger,
    wallet_id: str,
) -> None:
    await ledger.grant(wallet_id=wallet_id, amount_minor=1_000, idempotency_key=f"{wallet_id}:g")

    n = 20
    amount = 100  # exactly 10 of the 20 can succeed against a 1000 balance

    async def attempt(i: int) -> bool:
        try:
            await ledger.debit(
                wallet_id=wallet_id, amount_minor=amount, idempotency_key=f"{wallet_id}:par:{i}"
            )
            return True
        except InsufficientFunds:
            return False

    outcomes = await asyncio.gather(*(attempt(i) for i in range(n)))

    successes = sum(outcomes)
    # Discriminating: would FAIL if the FOR UPDATE lock were removed (overspend).
    assert successes == 10
    assert outcomes.count(False) == n - 10
    # Balance landed exactly at zero, never negative, and reconciles to the cent.
    assert await ledger.reconcile(wallet_id) == 0


async def test_system_wide_conservation_after_mixed_series(
    ledger: Ledger,
    wallet_id: str,
) -> None:
    await ledger.grant(wallet_id=wallet_id, amount_minor=1_000, idempotency_key=f"{wallet_id}:g")
    d1 = await ledger.debit(
        wallet_id=wallet_id, amount_minor=400, idempotency_key=f"{wallet_id}:d1"
    )
    c = await ledger.credit(
        wallet_id=wallet_id, amount_minor=250, idempotency_key=f"{wallet_id}:c1"
    )
    await ledger.rollback(op_ref=c.txn_id)  # reverse the win
    await ledger.debit(wallet_id=wallet_id, amount_minor=150, idempotency_key=f"{wallet_id}:d2")
    await ledger.rollback(op_ref=d1.txn_id)  # refund the first wager

    # Every op booked a player delta and its exact house negation: the closed
    # system nets to zero across ALL wallets and entries.
    assert await ledger.system_total() == 0
    # And the player wallet still reconciles against its own rows.
    await ledger.reconcile(wallet_id)


async def test_concurrent_same_key_debit_applies_exactly_once(
    ledger: Ledger,
    wallet_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The DB-level idempotency fence under CONCURRENCY.

    N racing debits share ONE identical idempotency key. Each opens its own
    session, so all N truly race: every racer runs the pre-read ``_find`` BEFORE
    acquiring the wallet's ``FOR UPDATE`` lock, so the losers carry a stale
    ``None`` past the fast path and collide on the UNIQUE constraint at INSERT —
    the ``IntegrityError`` branch (ledger.py) is the actual fence. Exactly one op
    must apply: one txn_id, one player row, balance moved by ONE debit.

    Discriminating: with the UNIQUE constraint dropped, the losers' inserts
    succeed and N rows apply — this assertion then fails (count != 1). That is
    the proof the test BINDS the DB fence, not the app-level pre-read.
    """
    n = 12
    amount = 100
    # Grant generously so ALL n debits COULD apply absent the fence (the RED
    # failure is then cleanly "n rows applied", never entangled with overspend).
    await ledger.grant(
        wallet_id=wallet_id, amount_minor=n * amount * 10, idempotency_key=f"{wallet_id}:g"
    )
    key = f"{wallet_id}:concurrent-debit"  # namespaced: absent until THIS op

    async def race() -> str:
        result = await ledger.debit(
            wallet_id=wallet_id, amount_minor=amount, idempotency_key=key
        )
        return result.txn_id

    txn_ids = await asyncio.gather(*(race() for _ in range(n)))

    # Every caller observed the SAME single txn_id (the op that won the race).
    assert len(set(txn_ids)) == 1
    # The fence in the DB: exactly one distinct txn and one player-side row.
    assert await _distinct_txn_ids_for_key(session_factory, key) == 1
    assert await _key_entry_count(session_factory, key) == 1
    # Applied ONCE: balance dropped by a single ``amount``, not by n * amount.
    assert await ledger.reconcile(wallet_id) == n * amount * 10 - amount


async def test_concurrent_multi_wallet_conservation_and_reconcile(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Concurrent DIFFERENT wallets contend on the shared house row; the closed
    double-entry system still nets to zero and every wallet reconciles.

    Each wallet runs its own ordered sequence (grant → debit → credit) so a debit
    never lands before its grant; the K sequences run concurrently, so all their
    ops contend on the ONE house wallet row's ``FOR UPDATE`` lock — coverage the
    single-wallet sequential conservation test doesn't reach.
    """
    k = 6
    wallet_ids = [await _make_wallet(session_factory) for _ in range(k)]

    async def sequence(wid: str) -> None:
        await ledger.grant(wallet_id=wid, amount_minor=1_000, idempotency_key=f"{wid}:g")
        await ledger.debit(wallet_id=wid, amount_minor=400, idempotency_key=f"{wid}:d")
        await ledger.credit(wallet_id=wid, amount_minor=250, idempotency_key=f"{wid}:c")

    await asyncio.gather(*(sequence(wid) for wid in wallet_ids))

    # System-wide Σ across ALL wallets/entries is zero (every op booked its exact
    # house negation, even under concurrent contention on the house row).
    assert await ledger.system_total() == 0
    # Each player wallet's projection equals Σ its own rows: 1000 - 400 + 250.
    for wid in wallet_ids:
        assert await ledger.reconcile(wid) == 850
