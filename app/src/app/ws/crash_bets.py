"""Crash betting + settlement — the money path layered over the S18 round actor.

Crash is structurally UNLIKE the per-user stateful saga (Mines/HiLo): ONE shared
actor-owned round holds MANY bets from MANY users (each may place several), the
seed is the round's CSPRNG seed (not per-user), ``C`` is fixed at open, and there
is NO per-user nonce and NO one-active-round guard. So this is a dedicated path —
it does NOT go through ``bet_loop.place_bet`` / the ``StatefulGame`` protocol.

It REUSES the money atoms verbatim, never re-deriving them: the ledger
(``Ledger.debit`` / ``Ledger.credit``), the idempotency-key scheme
(``bet_loop._idempotency_key`` = ``hash(betId, opType)``), the authoritative-config
lookup, the ``can_bet`` RG gate, and ``engine.money`` rounding/caps. Each placed
bet carries its OWN distinct ``betId`` (a player may place multiple per round), so
``hash(betId, op)`` stays unique per bet+op.

The four integrity rules (where the bugs and the review concentrate):

1. **Single terminal transition per bet.** A bet settles via THREE paths — manual
   cash-out (:func:`manual_cashout`), auto-cash-at-crash and crash-loss (both in
   :func:`settle_crash_round`). The idempotency key dedups a double *credit* but
   NOT a double *status decision*; so every path locks the bet row ``FOR UPDATE``,
   checks ``status == ACTIVE`` before transitioning, and credits OUTSIDE the lock
   (mirrors ``bet_loop.step_action``). A manual WIN is never overwritten LOST.
2. **Two DISTINCT inequalities.** Auto-cashout WINS iff ``t <= C`` (INCLUSIVE),
   pays ``t x``; manual cash-out WINS iff the server-stamped ``m < C`` (STRICT),
   pays ``m x``, else LOSS. They are not unified.
3. **Server-stamped multiplier.** The manual cash-out multiplier is the server's
   authoritative last-published tick (passed in by the actor), NEVER a client
   number — that is "no client-time cash-out" made concrete.
4. **Single-debit / single-credit, idempotent.** One ``WAGER`` at placement; AT
   MOST one ``WIN`` at settle. Settlement reads each bet's auto-cashout target
   from its persisted ``BET_PLACED`` audit row, so the crash sweep is a pure,
   idempotent replay (the property S20's reconciler builds on).

Persistence (S19 scope = money correctness only; S20 owns recovery/latency): the
round is persisted at START into the existing ``GameRound`` row (a MULTIPLAYER
discriminator) — its server-only ``server_state`` JSONB holds the per-round tuple
``{roundSeed, roundNumber, C}`` (never serialized to a client), so re-settle can
re-run ``CrashRound.open`` from the seed. No ``GET /state``, reconciler, or resume
is built here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent, Bet, GameLimit, GameRound, Wallet
from app.games.bet_loop import (
    BetRejected,
    GameDisabled,
    StakeOutOfRange,
    _idempotency_key,
    _latest_enabled_config,
)
from app.rg import can_bet
from app.wallet import Ledger, WalletNotFound
from app.ws.crash_core import CrashRound
from engine.money import apply_multiplier, cap

# The authoritative game id (the seeded GameConfig/GameLimit key, migration f6a7b8c9d0e1).
CRASH_GAME_ID = "originals.crash"


class CrashBetRejected(BetRejected):
    """A Crash bet was refused BEFORE any ledger movement (bad input / RG deny)."""


class CrashRoundNotFound(Exception):
    """No persisted Crash round for the given ``round_id`` (open it first)."""


class CrashBetNotFound(Exception):
    """No bet for ``(round_id, bet_id)`` (or it belongs to another round)."""


@dataclass(frozen=True)
class CrashBetResult:
    """The server-authoritative view of a placed/settled Crash bet."""

    bet_id: str
    round_id: str
    status: str  # ACTIVE | WON | LOST
    stake_minor: int
    payout_minor: int
    multiplier: float | None  # the cash-out multiplier (t or m) on a WIN


def _result(bet: Bet) -> CrashBetResult:
    return CrashBetResult(
        bet_id=bet.id,
        round_id=bet.round_id,
        status=bet.status,
        stake_minor=bet.stake_minor,
        payout_minor=bet.payout_minor or 0,
        multiplier=float(bet.multiplier) if bet.multiplier is not None else None,
    )


# --------------------------------------------------------------------------- #
# Round lifecycle — persist the round tuple at START (committed before outcome).
# --------------------------------------------------------------------------- #
async def open_crash_round(
    session_factory: async_sessionmaker[AsyncSession], rnd: CrashRound
) -> None:
    """Persist a freshly-opened round so bets can reference it and settlement can
    re-derive ``C``. Idempotent on ``round_id`` (a re-open is a no-op).

    ``server_state`` is the existing server-ONLY JSONB (never serialized to a
    client): it holds ``{roundSeed, roundNumber, C}`` — the per-round tuple S20's
    re-settle replays ``CrashRound.open`` from. The raw seed is the eventual public
    reveal, so storing it server-side leaks nothing.
    """
    async with session_factory() as session, session.begin():
        existing = await session.get(GameRound, rnd.round_id)
        if existing is not None:
            return
        session.add(
            GameRound(
                id=rnd.round_id,
                game_id=CRASH_GAME_ID,
                type="MULTIPLAYER",
                nonce=rnd.round_number,
                status="ACTIVE",
                server_state={
                    "roundSeed": rnd.round_server_seed.hex(),
                    "roundNumber": rnd.round_number,
                    "C": rnd.C,
                },
                created_at=datetime.now(UTC),
            )
        )


async def _round_C(session: AsyncSession, round_id: str) -> float:
    """The round's fixed crash point ``C`` from the persisted server-only tuple."""
    round_row = await session.get(GameRound, round_id)
    if round_row is None or round_row.server_state is None:
        raise CrashRoundNotFound(f"no crash round {round_id}")
    return float(round_row.server_state["C"])


# --------------------------------------------------------------------------- #
# Place a bet during WAITING — single debit, persist ACTIVE + the auto target.
# --------------------------------------------------------------------------- #
async def place_crash_bet(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    round_id: str,
    user_id: str,
    bet_id: str,
    stake_minor: int,
    currency: str = "GOLD",
    mode: str = "PLAY",
    auto_cashout: float | None = None,
) -> CrashBetResult:
    """Place one Crash bet (single debit), server-authoritative, idempotent on
    ``bet_id``. The optional ``auto_cashout`` target is persisted to the bet's
    ``BET_PLACED`` audit row, so settlement decides it as a pure replay."""
    if stake_minor <= 0:
        raise StakeOutOfRange(f"stake {stake_minor} must be positive")
    if auto_cashout is not None and auto_cashout < 1.0:
        raise CrashBetRejected(f"auto-cashout {auto_cashout} must be >= 1.00")

    async with session_factory() as session:
        cfg = await _latest_enabled_config(session, CRASH_GAME_ID)
        if cfg is None:
            raise GameDisabled(f"no enabled config for {CRASH_GAME_ID}")
        limit = await session.get(GameLimit, (CRASH_GAME_ID, currency))
        if limit is None:
            raise StakeOutOfRange(f"no limit for {CRASH_GAME_ID}/{currency}")
        if not (limit.min_bet <= stake_minor <= limit.max_bet):
            raise StakeOutOfRange(
                f"stake {stake_minor} outside [{limit.min_bet}, {limit.max_bet}]"
            )
        if await session.get(GameRound, round_id) is None:
            raise CrashRoundNotFound(f"no crash round {round_id}")
        wallet = await session.scalar(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency,
                Wallet.mode == mode,
            )
        )
        if wallet is None:
            raise WalletNotFound(f"no {currency}/{mode} wallet for user {user_id}")
        wallet_id = wallet.id
        existing = await session.get(Bet, bet_id)

    # Idempotent replay: a known bet returns its current projection, moves no money.
    if existing is not None:
        return _result(existing)

    decision = can_bet(
        user_id=user_id, game_id=CRASH_GAME_ID, stake_minor=stake_minor, currency=currency
    )
    if not decision.allowed:
        raise CrashBetRejected(decision.reason or "blocked by responsible-gaming policy")

    key_debit = _idempotency_key(bet_id, "WAGER")
    key_credit = _idempotency_key(bet_id, "WIN")

    # Single debit (InsufficientFunds aborts here — nothing persisted/stranded).
    await ledger.debit(
        wallet_id=wallet_id, amount_minor=stake_minor, idempotency_key=key_debit, ref=bet_id
    )

    now = datetime.now(UTC)
    try:
        async with session_factory() as session, session.begin():
            session.add(
                Bet(
                    id=bet_id,
                    round_id=round_id,
                    user_id=user_id,
                    wallet_id=wallet_id,
                    currency=currency,
                    mode=mode,
                    stake_minor=stake_minor,
                    status="ACTIVE",
                    idempotency_key_debit=key_debit,
                    idempotency_key_credit=key_credit,
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                AuditEvent(
                    id=uuid4().hex,
                    bet_id=bet_id,
                    round_id=round_id,
                    type="BET_PLACED",
                    payload={
                        "gameId": CRASH_GAME_ID,
                        "stakeMinor": stake_minor,
                        "currency": currency,
                        "autoCashout": auto_cashout,
                    },
                )
            )
    except IntegrityError:
        # A concurrent duplicate won the betId PK race (its debit deduped onto the
        # same key). Return its bet; a non-duplicate error surfaces (the debit
        # already committed, so a buried error must be visible, not swallowed).
        async with session_factory() as session:
            winner = await session.get(Bet, bet_id)
        if winner is None:
            raise
        return _result(winner)

    return CrashBetResult(
        bet_id=bet_id,
        round_id=round_id,
        status="ACTIVE",
        stake_minor=stake_minor,
        payout_minor=0,
        multiplier=None,
    )


# --------------------------------------------------------------------------- #
# Settlement primitive — one bet, one locked terminal transition, credit outside.
# --------------------------------------------------------------------------- #
async def _settle_bet(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    bet_id: str,
    win: bool,
    multiplier: float,
) -> CrashBetResult:
    """Transition ONE bet to its terminal status under a row lock, then credit a
    win OUTSIDE the lock (idempotent). The ``status == ACTIVE`` guard makes this a
    no-op on an already-terminal bet, so any settle path is a safe replay and a
    manual WIN can never be overwritten by the crash sweep (and vice versa)."""
    now = datetime.now(UTC)
    credit_req: tuple[str, int, str] | None = None

    async with session_factory() as session, session.begin():
        bet = await session.get(Bet, bet_id, with_for_update=True)
        if bet is None:
            raise CrashBetNotFound(f"no bet {bet_id}")
        if bet.status != "ACTIVE":
            # Already settled (manual cash-out, or a prior sweep): re-issue an
            # un-landed win credit, decide nothing new.
            if bet.status == "WON" and bet.payout_minor and bet.idempotency_key_credit:
                credit_req = (bet.wallet_id, bet.payout_minor, bet.idempotency_key_credit)
            result = _result(bet)
        elif win:
            limit = await session.get(GameLimit, (CRASH_GAME_ID, bet.currency))
            max_win = limit.max_win if limit is not None else 0
            payout = cap(apply_multiplier(bet.stake_minor, multiplier), max_win)
            bet.status = "WON"
            bet.payout_minor = payout
            bet.multiplier = Decimal(str(multiplier))
            bet.settled_at = now
            key_credit = bet.idempotency_key_credit or _idempotency_key(bet_id, "WIN")
            credit_req = (bet.wallet_id, payout, key_credit)
            result = _result(bet)
        else:
            bet.status = "LOST"
            bet.payout_minor = 0
            bet.multiplier = Decimal("0")
            bet.settled_at = now
            result = _result(bet)

    if credit_req is not None:
        wallet_id, amount, key_credit = credit_req
        if amount > 0:
            await ledger.credit(
                wallet_id=wallet_id,
                amount_minor=amount,
                idempotency_key=key_credit,
                ref=bet_id,
            )
    return result


# --------------------------------------------------------------------------- #
# Manual cash-out during RUNNING — server stamps the multiplier; m < C wins.
# --------------------------------------------------------------------------- #
async def manual_cashout(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    round_id: str,
    bet_id: str,
    stamped_multiplier: float,
) -> CrashBetResult:
    """Settle a manual cash-out. ``stamped_multiplier`` is the SERVER's authoritative
    last-published tick (never a client number). WIN iff ``stamped < C`` (STRICT),
    paying ``stamped x``; otherwise (the request landed at/after the crash) a LOSS.
    Idempotent: a duplicate returns the original and never double-credits."""
    async with session_factory() as session:
        bet = await session.get(Bet, bet_id)
        if bet is None or bet.round_id != round_id:
            raise CrashBetNotFound(f"no bet {bet_id} in round {round_id}")
        C = await _round_C(session, round_id)

    return await _settle_bet(
        session_factory,
        ledger,
        bet_id=bet_id,
        win=stamped_multiplier < C,  # STRICT — m == C is a loss
        multiplier=stamped_multiplier,
    )


# --------------------------------------------------------------------------- #
# Crash sweep — settle every still-ACTIVE bet against C (auto-cashout or loss).
# --------------------------------------------------------------------------- #
async def settle_crash_round(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    round_id: str,
) -> list[CrashBetResult]:
    """Settle all still-ACTIVE bets at crash. An auto-cashout bet WINS iff its
    target ``t <= C`` (INCLUSIVE), paying ``t x``; everything else LOSES. Each bet
    is its own locked, idempotent transition (read back from its ``BET_PLACED``
    audit), so re-running the sweep is a pure replay."""
    async with session_factory() as session:
        C = await _round_C(session, round_id)
        bet_ids = list(
            (
                await session.scalars(
                    select(Bet.id).where(
                        Bet.round_id == round_id, Bet.status == "ACTIVE"
                    )
                )
            ).all()
        )
        placed_rows = (
            await session.execute(
                select(AuditEvent.bet_id, AuditEvent.payload).where(
                    AuditEvent.round_id == round_id,
                    AuditEvent.type == "BET_PLACED",
                )
            )
        ).all()
        autos: dict[str | None, dict[str, Any] | None] = {
            bet_id: payload for bet_id, payload in placed_rows
        }

    results: list[CrashBetResult] = []
    for bet_id in bet_ids:
        payload: dict[str, Any] = dict(autos.get(bet_id) or {})
        auto = payload.get("autoCashout")
        win = auto is not None and float(auto) <= C  # INCLUSIVE
        results.append(
            await _settle_bet(
                session_factory,
                ledger,
                bet_id=bet_id,
                win=win,
                multiplier=float(auto) if auto is not None else 0.0,
            )
        )

    # Close the round (lifecycle / S20). Bets are already terminal above.
    async with session_factory() as session, session.begin():
        round_row = await session.get(GameRound, round_id, with_for_update=True)
        if round_row is not None and round_row.status != "SETTLED":
            round_row.status = "SETTLED"
            round_row.settled_at = datetime.now(UTC)

    return results


__all__ = [
    "CRASH_GAME_ID",
    "CrashBetNotFound",
    "CrashBetRejected",
    "CrashBetResult",
    "CrashRoundNotFound",
    "manual_cashout",
    "open_crash_round",
    "place_crash_bet",
    "settle_crash_round",
]
