"""S33 — poker lobby: matchmaking, buy-in, leave, and rake against a real ledger.

Security-grade money-movement invariants (CLAUDE.md):
- BUY-IN debits the player wallet (player → house) exactly once;
- LEAVE credits the player's remaining stack back (house → player) exactly once;
- RAKE is an explicit economy-sink debit booked to the SYSTEM/house account;
- every op is idempotent on ``hash(tableId, handNo, seat, opType)`` — a re-send
  collapses to the ORIGINAL ledger result and never double-applies;
- rake accounting reconciles to the cent over a series of hands; the ledger stays
  balanced (``Σ entries == 0``) and the player wallet reconciles to its projection.

These run against the REAL migrated Postgres (see ``tests/conftest.py``). Table ids
are generated per test so the table-wide UNIQUE idempotency keys never collide with
another test's rows (the shared DB is not truncated).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LedgerEntry, User, Wallet
from app.poker.lobby import Lobby, RakeConfig, Stakes, poker_op_key, poker_seat_op_key
from app.wallet import Ledger


# --------------------------------------------------------------------------- helpers
async def _funded_wallet(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger, amount_minor: int
) -> tuple[str, str]:
    """A fresh GOLD/PLAY wallet, optionally funded via a ledger GRANT (never a direct
    balance write). Returns ``(user_id, wallet_id)``."""
    wid = f"w-{uuid4().hex}"
    uid = f"u-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
    if amount_minor:
        await ledger.grant(
            wallet_id=wid, amount_minor=amount_minor, idempotency_key=f"fund:{wid}", ref="test"
        )
    return uid, wid


async def _balance(session_factory: async_sessionmaker[AsyncSession], wallet_id: str) -> int:
    async with session_factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert wallet is not None
        return wallet.balance_minor


def _unique_stakes() -> Stakes:
    """Per-test stakes so ``join`` opens a FRESH table — the shared DB is not truncated, so
    a fixed stakes level would reuse (and mis-seat into) a prior run's table."""
    base = uuid4().int % 1_000_000
    return Stakes(small_blind_minor=base + 1, big_blind_minor=(base + 1) * 2)


async def _key_count(
    session_factory: async_sessionmaker[AsyncSession], idempotency_key: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.idempotency_key == idempotency_key)
        )
    return int(n or 0)


# --------------------------------------------------------------------------- rake math
def test_rake_for_floors_and_caps() -> None:
    """Rake is integer minor units: floor(pot * rate) then cap; rake-free is always 0."""
    cfg = RakeConfig(rate_bps=500, cap_minor=300)  # 5%, capped at 3.00
    assert cfg.rake_for(1000) == 50
    assert cfg.rake_for(12345) == 300  # floor(617.25)=617 → capped at 300
    assert cfg.rake_for(0) == 0
    assert RakeConfig(rate_bps=0, cap_minor=0, rake_free=True).rake_for(100_000) == 0


# --------------------------------------------------------------------------- buy-in
async def test_buy_in_debits_wallet(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    seat = await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
    )

    assert seat.seat == 0
    assert seat.stack_minor == 5_000
    assert seat.ledger.replayed is False
    assert await _balance(session_factory, wid) == 5_000  # 10_000 − 5_000 buy-in


async def test_buy_in_resend_does_not_double_apply(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    first = await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
    )
    after_first = await _balance(session_factory, wid)

    second = await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
    )

    assert first.ledger.replayed is False
    assert second.ledger.replayed is True  # the dedup path was actually exercised
    assert await _balance(session_factory, wid) == after_first  # no second debit
    assert (
        await _key_count(session_factory, poker_seat_op_key(tid, 0, first.occupancy_id, "BUYIN"))
        == 1
    )


async def test_buy_in_resend_does_not_resurrect_a_diminished_stack(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """REGRESSION (replay money-printing): after a buy-in the player loses chips in play
    (``set_stack``). A re-sent buy-in must NOT re-finalize the seat back to the full buy-in
    amount — the leave must still credit only the DIMINISHED stack, never the original buy-in."""
    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=2_000
    )
    await lobby.set_stack(table_id=tid, seat=0, stack_minor=500)  # lost chips in play
    again = await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=2_000
    )
    assert again.ledger.replayed is True  # no re-charge

    before = await _balance(session_factory, wid)
    result = await lobby.leave(table_id=tid, hand_no=1, seat=0, user_id=uid)
    assert result is not None and result.delta_minor == 500  # diminished stack, NOT 2_000
    assert await _balance(session_factory, wid) == before + 500


# --------------------------------------------------------------------------- leave
async def test_leave_credits_remaining_stack(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
    )
    # The remaining stack differs from the buy-in (won chips during play).
    await lobby.set_stack(table_id=tid, seat=0, stack_minor=7_000)
    before = await _balance(session_factory, wid)  # 5_000

    result = await lobby.leave(table_id=tid, hand_no=1, seat=0, user_id=uid)

    assert result.replayed is False
    assert result.delta_minor == 7_000
    assert await _balance(session_factory, wid) == before + 7_000  # remaining stack returned


async def test_leave_resend_does_not_double_apply(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    seated = await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
    )
    first = await lobby.leave(table_id=tid, hand_no=1, seat=0, user_id=uid)
    after_first = await _balance(session_factory, wid)

    second = await lobby.leave(table_id=tid, hand_no=1, seat=0, user_id=uid)

    assert first is not None and first.replayed is False
    # vacated seat retains stack + occupancy id → recomputable
    assert second is not None and second.replayed is True
    assert await _balance(session_factory, wid) == after_first  # no second credit
    assert (
        await _key_count(session_factory, poker_seat_op_key(tid, 0, seated.occupancy_id, "LEAVE"))
        == 1
    )


async def test_leave_with_zero_stack_vacates_without_a_ledger_row(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """A busted player (stack 0) standing up is routine: it must vacate cleanly and book
    NO ledger row (the ledger rejects a non-positive amount)."""
    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    seated = await lobby.buy_in(
        table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
    )
    await lobby.set_stack(table_id=tid, seat=0, stack_minor=0)  # busted out
    before = await _balance(session_factory, wid)

    result = await lobby.leave(table_id=tid, hand_no=1, seat=0, user_id=uid)

    assert result is None  # nothing booked
    assert await _balance(session_factory, wid) == before
    assert (
        await _key_count(session_factory, poker_seat_op_key(tid, 0, seated.occupancy_id, "LEAVE"))
        == 0
    )
    # The seat is vacated: it no longer appears as occupied in the lobby listing.
    async with session_factory() as session:
        from app.db.models import PokerTable

        row = await session.get(PokerTable, tid)
        assert row is not None
        assert row.seats["0"]["vacated"] is True


async def test_unaffordable_buy_in_leaves_no_creditable_seat(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """Safe failure direction: a buy-in the player can't afford raises InsufficientFunds and
    rolls the reservation back (vacated, zero stack), so a later leave credits NOTHING — an
    unconfirmed seat can never be cashed out for chips the player never paid for."""
    from app.wallet import InsufficientFunds

    lobby = Lobby(session_factory, ledger)
    uid, wid = await _funded_wallet(session_factory, ledger, 1_000)  # too poor for 5_000
    tid = f"t-{uuid4().hex}"

    with pytest.raises(InsufficientFunds):
        await lobby.buy_in(
            table_id=tid, hand_no=0, seat=0, user_id=uid, wallet_id=wid, amount_minor=5_000
        )

    result = await lobby.leave(table_id=tid, hand_no=1, seat=0, user_id=uid)
    assert result is None  # the unpaid reservation is not creditable
    assert await _balance(session_factory, wid) == 1_000  # untouched: no debit, no credit


# ----------------------------------------------------------------- seat reuse (exploit guard)
async def test_seat_reuse_by_a_different_player_is_really_debited(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """REGRESSION (money-printing exploit): player A buys in and leaves; player B is matched
    into the SAME vacated seat. B's buy-in MUST be a real, fresh debit — NEVER an idempotent
    replay of A's original debit (which would seat B with a full stack having paid nothing)."""
    lobby = Lobby(session_factory, ledger)
    stakes = _unique_stakes()
    ua, wa = await _funded_wallet(session_factory, ledger, 10_000)
    ub, wb = await _funded_wallet(session_factory, ledger, 10_000)

    a = await lobby.join(user_id=ua, wallet_id=wa, stakes=stakes, buy_in_minor=2_000)
    await lobby.leave(table_id=a.table_id, hand_no=1, seat=a.seat, user_id=ua)

    before_b = await _balance(session_factory, wb)
    b = await lobby.join(user_id=ub, wallet_id=wb, stakes=stakes, buy_in_minor=2_000)

    assert b.table_id == a.table_id and b.seat == a.seat  # B reused A's vacated seat
    assert b.ledger.replayed is False  # B's buy-in is a REAL debit, not A's replay
    assert await _balance(session_factory, wb) == before_b - 2_000  # B actually paid


async def test_seat_reuse_does_not_print_chips(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """Cross-player conservation: A and B each buy in (2_000) and leave with the same
    unchanged stack at DIFFERENT hand numbers, sharing one reused seat. Each player's net
    wallet delta must be zero — no player may leave with chips the house never booked. Under
    the seat-reuse key collision, B's buy-in replays (no debit) while B's leave at a fresh
    hand number credits real chips → B nets +2_000 (printed money). The fix makes both nets 0."""
    lobby = Lobby(session_factory, ledger)
    stakes = _unique_stakes()
    ua, wa = await _funded_wallet(session_factory, ledger, 10_000)
    ub, wb = await _funded_wallet(session_factory, ledger, 10_000)
    start_a = await _balance(session_factory, wa)
    start_b = await _balance(session_factory, wb)

    a = await lobby.join(user_id=ua, wallet_id=wa, stakes=stakes, buy_in_minor=2_000)
    await lobby.leave(table_id=a.table_id, hand_no=1, seat=a.seat, user_id=ua)
    b = await lobby.join(user_id=ub, wallet_id=wb, stakes=stakes, buy_in_minor=2_000)
    await lobby.leave(table_id=b.table_id, hand_no=2, seat=b.seat, user_id=ub)

    delta_a = await _balance(session_factory, wa) - start_a
    delta_b = await _balance(session_factory, wb) - start_b
    assert delta_a == 0  # bought in, left with the same stack
    assert delta_b == 0  # B did NOT extract un-bought chips
    assert delta_a + delta_b == 0  # no chips printed across the two players
    assert await ledger.system_total() == 0  # closed double-entry system


# --------------------------------------------------------------------------- rake
async def test_rake_free_books_nothing(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger, rake=RakeConfig(rate_bps=0, cap_minor=0, rake_free=True))
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"
    before = await _balance(session_factory, wid)

    result = await lobby.take_rake(
        table_id=tid, hand_no=0, seat=0, wallet_id=wid, pot_minor=4_000
    )

    assert result.rake_minor == 0
    assert result.ledger is None
    assert await _balance(session_factory, wid) == before  # nothing booked
    assert await _key_count(session_factory, poker_op_key(tid, 0, 0, "RAKE")) == 0


async def test_rake_resend_does_not_double_apply(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger, rake=RakeConfig(rate_bps=500, cap_minor=300))
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)
    tid = f"t-{uuid4().hex}"

    first = await lobby.take_rake(
        table_id=tid, hand_no=3, seat=2, wallet_id=wid, pot_minor=2_000
    )
    after_first = await _balance(session_factory, wid)

    second = await lobby.take_rake(
        table_id=tid, hand_no=3, seat=2, wallet_id=wid, pot_minor=2_000
    )

    assert first.rake_minor == 100  # 5% of 2_000
    assert first.ledger is not None and first.ledger.replayed is False
    assert second.ledger is not None and second.ledger.replayed is True
    assert await _balance(session_factory, wid) == after_first  # no second debit
    assert await _key_count(session_factory, poker_op_key(tid, 3, 2, "RAKE")) == 1


async def test_rake_reconciles_over_a_series_of_hands(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    rake_cfg = RakeConfig(rate_bps=500, cap_minor=300)
    lobby = Lobby(session_factory, ledger, rake=rake_cfg)
    uid, wid = await _funded_wallet(session_factory, ledger, 1_000_000)
    tid = f"t-{uuid4().hex}"

    pots = [1_000, 2_000, 5_000, 12_345, 100]
    expected = sum(rake_cfg.rake_for(p) for p in pots)

    booked = 0
    for hand_no, pot in enumerate(pots):
        result = await lobby.take_rake(
            table_id=tid, hand_no=hand_no, seat=0, wallet_id=wid, pot_minor=pot
        )
        booked += result.rake_minor

    assert booked == expected

    # Sum the player-side RAKE rows for THIS table's keys (deltas, not the shared
    # cross-test house balance): they must total exactly −expected.
    keys = [poker_op_key(tid, h, 0, "RAKE") for h in range(len(pots))]
    async with session_factory() as session:
        rake_rows_total = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
                LedgerEntry.idempotency_key.in_(keys)
            )
        )
    assert int(rake_rows_total or 0) == -expected

    await ledger.reconcile(wid)  # projection == Σ entries for the player
    assert await ledger.system_total() == 0  # closed double-entry system


# --------------------------------------------------------------------------- matchmaking
async def test_join_assigns_seats_by_stakes(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger)
    stakes = _unique_stakes()
    other_stakes = _unique_stakes()

    u1, w1 = await _funded_wallet(session_factory, ledger, 10_000)
    u2, w2 = await _funded_wallet(session_factory, ledger, 10_000)
    u3, w3 = await _funded_wallet(session_factory, ledger, 10_000)

    a1 = await lobby.join(user_id=u1, wallet_id=w1, stakes=stakes, buy_in_minor=2_000)
    a2 = await lobby.join(user_id=u2, wallet_id=w2, stakes=stakes, buy_in_minor=2_000)
    a3 = await lobby.join(user_id=u3, wallet_id=w3, stakes=other_stakes, buy_in_minor=2_000)

    # Same stakes → same table, distinct seats; the buy-in debited each wallet once.
    assert a1.table_id == a2.table_id
    assert {a1.seat, a2.seat} == {0, 1}
    assert a3.table_id != a1.table_id  # different stakes seat at a different table
    assert await _balance(session_factory, w1) == 8_000

    tables = await lobby.list_tables(stakes=stakes)
    assert a1.table_id in {t.table_id for t in tables}
    assert all(t.stakes == stakes for t in tables)


async def test_join_is_idempotent_for_a_seated_user(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    lobby = Lobby(session_factory, ledger)
    stakes = _unique_stakes()
    uid, wid = await _funded_wallet(session_factory, ledger, 10_000)

    first = await lobby.join(user_id=uid, wallet_id=wid, stakes=stakes, buy_in_minor=2_000)
    after_first = await _balance(session_factory, wid)
    second = await lobby.join(user_id=uid, wallet_id=wid, stakes=stakes, buy_in_minor=2_000)

    assert second.table_id == first.table_id
    assert second.seat == first.seat
    assert second.ledger.replayed is True
    assert await _balance(session_factory, wid) == after_first  # no second buy-in
