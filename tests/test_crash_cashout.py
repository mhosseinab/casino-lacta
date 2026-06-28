"""S19 — Crash betting + cash-out: money correctness against a real migrated DB.

These exercise the dedicated Crash betting path (``app.ws.crash_bets`` + the
``CrashActor`` betting methods) — NOT the per-user stateful saga. One shared
actor-owned round, many bets; ``C`` fixed at open by the pure core. The four
invariants the realtime-integrity + ledger reviewers hunt for:

* **Two distinct inequalities.** Auto-cashout WINS iff target ``t <= C`` (INCLUSIVE),
  pays ``t x``; manual cash-out WINS iff the server-stamped ``m < C`` (STRICT), pays
  ``m x``, else it's a LOSS. Both boundaries are asserted against the round's ACTUAL
  ``C`` (``t == C`` auto -> WIN; ``m == C`` manual -> LOSS).
* **Single terminal transition per bet.** A manually-cashed (WON) bet is NEVER
  overwritten to LOST by the crash sweep, and credits exactly once.
* **Server-stamped multiplier.** ``cash_out`` takes ONLY a ``betId`` and stamps the
  actor's last-published tick — never a client-sent number.
* **Single-debit / single-credit, idempotent.** Exactly one ``WAGER`` ledger row per
  placed bet (keyed ``hash(betId,"WAGER")``) and AT MOST one ``WIN`` row (keyed
  ``hash(betId,"WIN")``) — a duplicate cash-out collapses onto the same key.

Run against the alembic-migrated Postgres (the conftest fixture); ``originals.crash``
is config/limit-seeded by migration ``f6a7b8c9d0e1``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Bet, LedgerEntry, User, Wallet
from app.games.bet_loop import _idempotency_key
from app.wallet import Ledger
from app.ws.crash import CrashActor, CrashBettingClosed, CrashNotRunning
from app.ws.crash_bets import (
    CRASH_GAME_ID,
    _settle_bet,
    manual_cashout,
    open_crash_round,
    place_crash_bet,
    settle_crash_round,
)
from app.ws.crash_core import CrashRound

EDGE = 0.01
# A fixed per-round seed; C is read from the opened round (never hardcoded — the
# t == C / m == C boundary tests need the EXACT float the core computed).
_SEED = b"s19-crash-cashout-seed"
_START_BALANCE = 1_000_000


@pytest.fixture
async def funded_user(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> AsyncIterator[tuple[str, str]]:
    """A fresh user with a funded GOLD/PLAY wallet. Returns ``(user_id, wallet_id)``."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(
            Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0)
        )
    await ledger.grant(
        wallet_id=wid, amount_minor=_START_BALANCE, idempotency_key=f"grant:{uid}", ref=uid
    )
    yield uid, wid


async def _open_round(
    session_factory: async_sessionmaker[AsyncSession],
    round_number: int = 1,
    *,
    min_c: float = 1.0,
) -> CrashRound:
    """Open + persist a round. ``min_c`` retries fresh round_ids until C >= min_c so a
    test that derives an auto-cashout/stamp from ``C`` (e.g. ``round(C - 0.01, 2)``)
    never lands below the ``>= 1.00`` placement guard when a random round_id yields the
    instant-bust C == 1.00 (~2% of ids)."""
    while True:
        rnd = CrashRound.open(
            round_server_seed=_SEED,
            round_id=f"round-{uuid4().hex}",
            round_number=round_number,
            edge=EDGE,
        )
        if rnd.C >= min_c:
            break
    await open_crash_round(session_factory, rnd)
    return rnd


async def _ledger_count(
    session_factory: async_sessionmaker[AsyncSession], idempotency_key: str
) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.idempotency_key == idempotency_key)
            )
            or 0
        )


async def _balance(
    session_factory: async_sessionmaker[AsyncSession], wallet_id: str
) -> int:
    async with session_factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert wallet is not None
        return wallet.balance_minor


async def _bet_status(
    session_factory: async_sessionmaker[AsyncSession], bet_id: str
) -> str:
    async with session_factory() as session:
        bet = await session.get(Bet, bet_id)
        assert bet is not None
        return bet.status


# --------------------------------------------------------------------------- #
# Auto-cashout exactness — t wins iff t <= C (INCLUSIVE), pays t x.
# --------------------------------------------------------------------------- #
async def test_auto_cashout_wins_iff_target_le_C(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    # min_c=1.5 so round(C - 0.01, 2) stays >= 1.00 (the placement guard) — without it
    # an instant-bust C == 1.00 makes the edge-below target 0.99 and trips the guard.
    rnd = await _open_round(session_factory, min_c=1.5)
    C = rnd.C
    stake = 1000

    win_id = f"bet-{uuid4().hex}"  # t == C exactly: INCLUSIVE -> WIN
    edge_below_id = f"bet-{uuid4().hex}"  # t just below C -> WIN
    lose_id = f"bet-{uuid4().hex}"  # t just above C -> LOSS

    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=win_id, stake_minor=stake, auto_cashout=C,
    )
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=edge_below_id, stake_minor=stake, auto_cashout=round(C - 0.01, 2),
    )
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=lose_id, stake_minor=stake, auto_cashout=round(C + 0.01, 2),
    )

    await settle_crash_round(session_factory, ledger, round_id=rnd.round_id)

    assert await _bet_status(session_factory, win_id) == "WON"  # t == C -> WIN
    assert await _bet_status(session_factory, edge_below_id) == "WON"
    assert await _bet_status(session_factory, lose_id) == "LOST"  # t > C -> LOSS

    # Winner pays floor(stake * t); loser credits nothing.
    from engine.money import apply_multiplier

    assert await _ledger_count(session_factory, _idempotency_key(win_id, "WIN")) == 1
    assert await _ledger_count(session_factory, _idempotency_key(lose_id, "WIN")) == 0
    async with session_factory() as session:
        won = await session.get(Bet, win_id)
        assert won is not None
        assert won.payout_minor == apply_multiplier(stake, C)


# --------------------------------------------------------------------------- #
# Manual cash-out: m < C wins; m == C is a LOSS (STRICT); m >= C is a LOSS.
# --------------------------------------------------------------------------- #
async def test_manual_cashout_loss_when_tick_ge_C(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    C = rnd.C
    stake = 1000

    at_C_id = f"bet-{uuid4().hex}"  # stamped == C -> LOSS (strict <)
    above_C_id = f"bet-{uuid4().hex}"  # stamped > C -> LOSS

    for bet_id in (at_C_id, above_C_id):
        await place_crash_bet(
            session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
            bet_id=bet_id, stake_minor=stake, auto_cashout=None,
        )

    r1 = await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=at_C_id, stamped_multiplier=C
    )
    r2 = await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=above_C_id,
        stamped_multiplier=round(C + 1.0, 2),
    )

    assert r1.status == "LOST"
    assert r2.status == "LOST"
    assert await _ledger_count(session_factory, _idempotency_key(at_C_id, "WIN")) == 0
    assert await _ledger_count(session_factory, _idempotency_key(above_C_id, "WIN")) == 0


async def test_manual_cashout_below_C_wins_at_stamped_multiplier(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    stamp = round(rnd.C - 0.01, 2)  # strictly below C -> WIN at the stamped value
    assert stamp < rnd.C

    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=None,
    )
    before = await _balance(session_factory, wallet_id)
    res = await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=bet_id, stamped_multiplier=stamp
    )

    from engine.money import apply_multiplier

    assert res.status == "WON"
    assert res.payout_minor == apply_multiplier(stake, stamp)
    assert await _balance(session_factory, wallet_id) == before + apply_multiplier(stake, stamp)


# --------------------------------------------------------------------------- #
# Duplicate cash-out never double-credits (same betId+opType collapses).
# --------------------------------------------------------------------------- #
async def test_duplicate_cashout_never_double_credits(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    stamp = round(rnd.C - 0.01, 2)

    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=None,
    )
    before = await _balance(session_factory, wallet_id)

    first = await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=bet_id, stamped_multiplier=stamp
    )
    second = await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=bet_id, stamped_multiplier=stamp
    )

    from engine.money import apply_multiplier

    payout = apply_multiplier(stake, stamp)
    assert first.status == second.status == "WON"
    assert first.payout_minor == second.payout_minor == payout
    # Exactly ONE credit booked despite two cash-out calls.
    assert await _ledger_count(session_factory, _idempotency_key(bet_id, "WIN")) == 1
    assert await _balance(session_factory, wallet_id) == before + payout


# --------------------------------------------------------------------------- #
# Two bets by one player in one round settle independently.
# --------------------------------------------------------------------------- #
async def test_two_bets_one_round_settle_independently(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    stamp = round(rnd.C - 0.01, 2)

    cashed_id = f"bet-{uuid4().hex}"  # manually cashed -> WON
    rider_id = f"bet-{uuid4().hex}"  # no auto, rides to crash -> LOST

    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=cashed_id, stake_minor=stake, auto_cashout=None,
    )
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=rider_id, stake_minor=stake, auto_cashout=None,
    )

    await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=cashed_id, stamped_multiplier=stamp
    )
    await settle_crash_round(session_factory, ledger, round_id=rnd.round_id)

    from engine.money import apply_multiplier

    assert await _bet_status(session_factory, cashed_id) == "WON"
    assert await _bet_status(session_factory, rider_id) == "LOST"
    # Independent, distinct idempotency keys; one credit for the winner, none for the rider.
    assert _idempotency_key(cashed_id, "WAGER") != _idempotency_key(rider_id, "WAGER")
    assert await _ledger_count(session_factory, _idempotency_key(cashed_id, "WIN")) == 1
    assert await _ledger_count(session_factory, _idempotency_key(rider_id, "WIN")) == 0
    # Each bet debited exactly once.
    assert await _ledger_count(session_factory, _idempotency_key(cashed_id, "WAGER")) == 1
    assert await _ledger_count(session_factory, _idempotency_key(rider_id, "WAGER")) == 1
    # Net: -2*stake (two debits) + floor(stake*stamp) (one win).
    expected = _START_BALANCE - 2 * stake + apply_multiplier(stake, stamp)
    assert await _balance(session_factory, wallet_id) == expected


# --------------------------------------------------------------------------- #
# Single terminal transition: a manual WIN is not overwritten LOST by the sweep.
# --------------------------------------------------------------------------- #
async def test_manual_win_not_overwritten_by_crash_sweep(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    stamp = round(rnd.C - 0.01, 2)

    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=None,
    )
    await manual_cashout(
        session_factory, ledger, round_id=rnd.round_id, bet_id=bet_id, stamped_multiplier=stamp
    )
    # The crash sweep runs over the whole round AFTER the manual cash-out.
    await settle_crash_round(session_factory, ledger, round_id=rnd.round_id)

    assert await _bet_status(session_factory, bet_id) == "WON"  # NOT overwritten LOST
    assert await _ledger_count(session_factory, _idempotency_key(bet_id, "WIN")) == 1


# --------------------------------------------------------------------------- #
# Single debit per placed bet; an idempotent re-place returns the original.
# --------------------------------------------------------------------------- #
async def test_place_bet_single_debit_and_idempotent_replace(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    bet_id = f"bet-{uuid4().hex}"

    first = await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=2.0,
    )
    second = await place_crash_bet(  # idempotent re-send returns the ORIGINAL
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=2.0,
    )

    assert first.status == second.status == "ACTIVE"
    assert await _ledger_count(session_factory, _idempotency_key(bet_id, "WAGER")) == 1
    assert await _balance(session_factory, wallet_id) == _START_BALANCE - stake


# --------------------------------------------------------------------------- #
# Server-stamped multiplier: cash_out takes ONLY betId; it stamps the actor's
# last-published tick (no client-sent number can decide a cash-out).
# --------------------------------------------------------------------------- #
async def test_cash_out_stamps_actor_tracked_tick(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    tick = round(rnd.C - 0.01, 2)
    assert tick < rnd.C

    actor = CrashActor(_DummyPublisher(), session_factory=session_factory, ledger=ledger)
    # Drive the actor into a RUNNING round (the betting + tick state the WS path sees).
    actor._round = rnd  # noqa: SLF001 - test drives the actor's tracked round
    bet_id = f"bet-{uuid4().hex}"
    await actor.place_bet(
        user_id=user_id, bet_id=bet_id, stake_minor=stake, auto_cashout=None
    )
    actor._round = rnd.lock().start()  # noqa: SLF001 - WAITING -> LOCKED -> RUNNING
    actor._current_multiplier = tick  # noqa: SLF001 - the last PUBLISHED cosmetic tick

    res = await actor.cash_out(bet_id=bet_id)  # NOTE: only betId — no client multiplier

    from engine.money import apply_multiplier

    assert res.status == "WON"
    assert res.payout_minor == apply_multiplier(stake, tick)  # stamped == tracked tick


class _DummyPublisher:
    async def publish(self, channel: str, message: str) -> None:  # noqa: ARG002
        return None


def test_crash_game_id_is_originals_crash() -> None:
    assert CRASH_GAME_ID == "originals.crash"


# --------------------------------------------------------------------------- #
# Server-authoritative TIMING gates — the controls that stop latency/information
# arbitrage (betting AFTER watching the multiplier climb; cashing out outside the
# run window). Each test below FAILS if its specific guard in
# CrashActor.place_bet / cash_out is removed (non-vacuity confirmed).
# --------------------------------------------------------------------------- #
def _make_actor(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> CrashActor:
    return CrashActor(_DummyPublisher(), session_factory=session_factory, ledger=ledger)


def _waiting_round() -> CrashRound:
    return CrashRound.open(
        round_server_seed=_SEED, round_id=f"round-{uuid4().hex}", round_number=1, edge=EDGE
    )


async def test_place_bet_rejected_during_running(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """Betting is WAITING-only: a placement once the round is RUNNING (multiplier
    visibly climbing) is rejected — no information-arbitrage entry."""
    actor = _make_actor(session_factory, ledger)
    actor._round = _waiting_round().lock().start()  # noqa: SLF001 - RUNNING
    with pytest.raises(CrashBettingClosed):
        await actor.place_bet(user_id="u", bet_id=f"bet-{uuid4().hex}", stake_minor=1000)


async def test_place_bet_rejected_when_no_round(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """No round in progress → no placement (the round-is-None arm of the guard)."""
    actor = _make_actor(session_factory, ledger)
    assert actor._round is None  # noqa: SLF001
    with pytest.raises(CrashBettingClosed):
        await actor.place_bet(user_id="u", bet_id=f"bet-{uuid4().hex}", stake_minor=1000)


async def test_cash_out_rejected_during_waiting(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """Cash-out is RUNNING-only: a cash-out during the WAITING betting window is
    rejected (you cannot exit a run that has not started)."""
    actor = _make_actor(session_factory, ledger)
    actor._round = _waiting_round()  # noqa: SLF001 - WAITING
    with pytest.raises(CrashNotRunning):
        await actor.cash_out(bet_id=f"bet-{uuid4().hex}")


async def test_cash_out_rejected_after_crashed(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """A cash-out after the round CRASHED is rejected by the timing gate (the late
    request is a loss handled by the sweep, never a post-crash exit)."""
    actor = _make_actor(session_factory, ledger)
    actor._round = _waiting_round().lock().start().crash()  # noqa: SLF001 - CRASHED
    with pytest.raises(CrashNotRunning):
        await actor.cash_out(bet_id=f"bet-{uuid4().hex}")


async def test_cash_out_rejected_when_no_round(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> None:
    """No round in progress → no cash-out (the round-is-None arm of the guard)."""
    actor = _make_actor(session_factory, ledger)
    assert actor._round is None  # noqa: SLF001
    with pytest.raises(CrashNotRunning):
        await actor.cash_out(bet_id=f"bet-{uuid4().hex}")


# --------------------------------------------------------------------------- #
# Single-terminal-transition guard, bound DIRECTLY on _settle_bet. Unlike the
# sweep test (whose ACTIVE-filtered SELECT means _settle_bet is never called on a
# terminal bet), these invoke _settle_bet on an ALREADY-terminal bet with a
# CONFLICTING decision — so they bind the `status != "ACTIVE"` guard itself.
# Neuter that guard to `if False:` and both FAIL (status flips / a row is added).
# --------------------------------------------------------------------------- #
async def test_settle_bet_guard_keeps_won_bet_won(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=None,
    )

    # First: settle it WON. Then a CONFLICTING second settle (win=False) must be a
    # no-op — the guard refuses to re-decide a terminal bet.
    won = await _settle_bet(session_factory, ledger, bet_id=bet_id, win=True, multiplier=2.0)
    conflicting = await _settle_bet(
        session_factory, ledger, bet_id=bet_id, win=False, multiplier=0.0
    )

    assert won.status == "WON"
    assert conflicting.status == "WON"  # NOT flipped to LOST
    assert await _bet_status(session_factory, bet_id) == "WON"
    assert await _ledger_count(session_factory, _idempotency_key(bet_id, "WIN")) == 1


async def test_settle_bet_guard_keeps_lost_bet_lost(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    stake = 1000
    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=stake, auto_cashout=None,
    )

    # First: settle it LOST. Then a CONFLICTING second settle (win=True) must be a
    # no-op — the guard refuses to pay out an already-lost bet.
    lost = await _settle_bet(session_factory, ledger, bet_id=bet_id, win=False, multiplier=0.0)
    conflicting = await _settle_bet(
        session_factory, ledger, bet_id=bet_id, win=True, multiplier=2.0
    )

    assert lost.status == "LOST"
    assert conflicting.status == "LOST"  # NOT flipped to WON
    assert await _bet_status(session_factory, bet_id) == "LOST"
    assert await _ledger_count(session_factory, _idempotency_key(bet_id, "WIN")) == 0
