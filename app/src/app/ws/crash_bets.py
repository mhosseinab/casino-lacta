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

Persistence: the round is persisted at START into the existing ``GameRound`` row (a
MULTIPLAYER discriminator) — its server-only ``server_state`` JSONB holds the
per-round tuple ``{roundSeed, roundNumber, C}`` (never serialized to a client), so
re-settle re-reads ``C`` from the seed.

S20 — recovery/resume/latency built ON the same idempotent settlement path (never a
second money path): :func:`crash_state` (the ``GET /state`` resume projection, with
status-keyed redaction + an identity fence), :func:`next_crash_round_number` +
:func:`recover_crash` (restart-safe: re-settle orphans, continue the counter from the
DB so an id is never regenerated over a stale ``C``), and :func:`reconcile_crash_bets`
(bets stuck non-terminal past a TTL). All re-settlement routes through the SAME
:func:`settle_crash_round` replay — the reconciler/recovery never re-decide an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
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
from engine.fairness import commit
from engine.money import apply_multiplier, cap

# The authoritative game id (the seeded GameConfig/GameLimit key, migration f6a7b8c9d0e1).
CRASH_GAME_ID = "originals.crash"

# Reconciler TTL — the config SEAM (a named knob + a per-call override, not a buried
# literal): a still-ACTIVE bet whose round has been around longer than this is
# considered stuck and is force-settled via the one settlement path. Boot recovery
# passes ``ttl_seconds=0`` (re-settle every orphan; on boot nothing is in-progress);
# the periodic reconciler uses this default so it never disturbs a live round.
DEFAULT_RECONCILE_TTL_SECONDS = 300


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

    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id=CRASH_GAME_ID,
        stake_minor=stake_minor,
        currency=currency,
    )
    if not decision.allowed:
        raise CrashBetRejected(str(decision.reason) if decision.reason else "RG_BLOCKED")

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


# --------------------------------------------------------------------------- #
# Resume — GET /state projection: the CURRENT shared round + the caller's bets.
# --------------------------------------------------------------------------- #
async def crash_state(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    live_multiplier: float | None = None,
) -> dict[str, Any]:
    """The server-authoritative resume projection for a reconnecting client.

    The round is the CURRENT shared round (latest by ``nonce`` — Crash is
    MULTIPLAYER, not caller-scoped), returned even when the caller has no bets.
    ``bets`` is the caller's still-ACTIVE bets in that round (an IDENTITY fence —
    a caller never sees another player's positions).

    REDACTION keys off the PERSISTED status (the recovery authority; the in-memory
    actor may be absent): while the round is live (DB ``ACTIVE`` — covers
    WAITING/RUNNING/CRASHED-pre-settle) the projection WITHHOLDS ``C`` and the raw
    seed, exposing only the commitment hash + the cosmetic multiplier (what the
    live tick/round events already show). Once ``SETTLED`` (post-reveal: betting +
    cash-out are closed and the crash event reveals the seed) it MAY disclose
    ``serverSeed`` + ``crashPoint`` — no exploitable advantage remains.

    ``live_multiplier`` is the actor's last-published cosmetic tick when an
    in-process actor supplies it (``None`` otherwise — the unwired-actor fence); it
    is public either way (it is never an input to ``C``).
    """
    async with session_factory() as session:
        round_row = await session.scalar(
            select(GameRound)
            .where(
                GameRound.game_id == CRASH_GAME_ID,
                GameRound.type == "MULTIPLAYER",
            )
            .order_by(GameRound.created_at.desc(), GameRound.nonce.desc())
            .limit(1)
        )
        if round_row is None:
            return {"round": None, "bets": []}
        round_id = round_row.id
        bet_rows = list(
            (
                await session.scalars(
                    select(Bet).where(
                        Bet.round_id == round_id,
                        Bet.user_id == user_id,
                        Bet.status == "ACTIVE",
                    )
                )
            ).all()
        )
        audit_rows = (
            await session.execute(
                select(AuditEvent.bet_id, AuditEvent.payload).where(
                    AuditEvent.round_id == round_id,
                    AuditEvent.type == "BET_PLACED",
                )
            )
        ).all()

    autos: dict[str | None, float | None] = {
        bet_id: (payload or {}).get("autoCashout") for bet_id, payload in audit_rows
    }
    server_state = round_row.server_state or {}
    is_settled = round_row.status == "SETTLED"
    round_view: dict[str, Any] = {
        "roundId": round_id,
        "roundNumber": round_row.nonce,
        "serverSeedHash": commit(bytes.fromhex(server_state["roundSeed"])),
        "status": round_row.status,
        "multiplier": live_multiplier,
    }
    if is_settled:
        # Post-reveal: the seed + outcome are public (the crash event revealed them).
        round_view["serverSeed"] = server_state["roundSeed"]
        round_view["crashPoint"] = float(server_state["C"])
    bets = [
        {
            "betId": bet.id,
            "stakeMinor": bet.stake_minor,
            "status": bet.status,
            "autoCashout": autos.get(bet.id),
        }
        for bet in bet_rows
    ]
    return {"round": round_view, "bets": bets}


# --------------------------------------------------------------------------- #
# Restart recovery — re-settle orphaned rounds + recover the round counter.
# --------------------------------------------------------------------------- #
async def next_crash_round_number(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """The next round number = ``max(nonce) + 1`` over persisted crash rounds (``1``
    if none). Recovering the counter from the DB is what stops a restarted actor
    from regenerating an existing ``round-{n}`` id (which would no-op
    ``open_crash_round`` and broadcast a new curve over the OLD persisted ``C``)."""
    async with session_factory() as session:
        max_nonce = await session.scalar(
            select(func.max(GameRound.nonce)).where(GameRound.game_id == CRASH_GAME_ID)
        )
    return int(max_nonce or 0) + 1


async def _stuck_round_ids(
    session: AsyncSession, *, ttl_seconds: int
) -> list[str]:
    """Crash round ids that still hold ≥1 ACTIVE bet AND are either already terminal
    (a partial sweep) or older than the TTL (orphaned). The single membership test
    both boot recovery (``ttl_seconds=0`` → every orphan) and the periodic
    reconciler share."""
    cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
    rows = await session.scalars(
        select(GameRound.id)
        .join(Bet, Bet.round_id == GameRound.id)
        .where(
            GameRound.game_id == CRASH_GAME_ID,
            Bet.status == "ACTIVE",
            (GameRound.status == "SETTLED") | (GameRound.created_at <= cutoff),
        )
        .distinct()
    )
    return list(rows.all())


async def reconcile_crash_bets(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    ttl_seconds: int = DEFAULT_RECONCILE_TTL_SECONDS,
) -> list[str]:
    """Find rounds with bets stuck non-terminal (past the TTL, or in an already
    terminal round) and re-settle each via the ONE settlement path
    (``settle_crash_round`` — a pure idempotent replay). Returns the round ids it
    reconciled."""
    async with session_factory() as session:
        round_ids = await _stuck_round_ids(session, ttl_seconds=ttl_seconds)
    for round_id in round_ids:
        await settle_crash_round(session_factory, ledger, round_id=round_id)
    return round_ids


async def recover_crash(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> list[str]:
    """Boot recovery: re-settle EVERY orphaned crash round (``ttl_seconds=0`` — on a
    fresh boot nothing is legitimately in-progress) before any new round opens. The
    committed seed already fixed ``C``, so each re-settle is deterministic. Reuses
    the reconciler's one path (DRY)."""
    return await reconcile_crash_bets(session_factory, ledger, ttl_seconds=0)


__all__ = [
    "CRASH_GAME_ID",
    "DEFAULT_RECONCILE_TTL_SECONDS",
    "CrashBetNotFound",
    "CrashBetRejected",
    "CrashBetResult",
    "CrashRoundNotFound",
    "crash_state",
    "manual_cashout",
    "next_crash_round_number",
    "open_crash_round",
    "place_crash_bet",
    "recover_crash",
    "reconcile_crash_bets",
    "settle_crash_round",
]
