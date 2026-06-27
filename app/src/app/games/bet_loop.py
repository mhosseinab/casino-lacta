"""The shared bet loop — the single place a bet becomes settled credits.

Order (an idempotent saga; the ledger owns its own transactions, deduped by
``idempotencyKey = hash(betId, opType)`` so every step applies exactly once):

1. **Validate** — resolve the game (registry), load the authoritative DB
   ``GameConfig`` (latest enabled → its ``version`` is the audited
   ``configVersion``) + ``GameLimit``; reject a stake outside ``[min_bet,
   max_bet]`` BEFORE any money moves.
2. **Idempotent replay** — a known ``betId`` returns the ORIGINAL bet (and
   re-issues the idempotent win credit if it was a WON bet that never settled the
   credit — self-healing a crash between "decided" and "credited").
3. **RG gate** — ``can_bet`` BEFORE the debit; a deny moves no credits.
4. **Single debit** — stake leaves the wallet exactly once (``hash(betId,
   "WAGER")``); ``InsufficientFunds`` aborts here, nothing persisted.
5. **Reserve nonce + derive + persist — ONE transaction.** The per-(user,
   server-seed) ``NonceCounter`` is locked ``FOR UPDATE``, bumped, the outcome is
   derived from ``create_rng(serverSeed, clientSeed, nonce)`` (pure engine), and
   ``Round``/``Bet``/``AuditEvent`` are inserted keyed on ``betId`` — all atomic.
   A concurrent duplicate loses the ``betId`` PK race, so ITS nonce bump rolls
   back too: both attempts share one nonce, one outcome (no divergence).
6. **Single credit** — a win credits exactly once (``hash(betId, "WIN")``).

Determinism makes this self-healing: the persisted ``(serverSeed, clientSeed,
nonce)`` recompute the same outcome on any retry. Server-authoritative
throughout — the client supplies only intent + stake; the SERVER derives the
outcome and multiplier.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AuditEvent,
    Bet,
    ClientSeed,
    GameLimit,
    GameRound,
    NonceCounter,
    ServerSeed,
    Wallet,
)
from app.db.models import (
    GameConfig as GameConfigRow,
)
from app.rg import can_bet
from app.wallet import Ledger, WalletNotFound
from engine.money import apply_multiplier, cap
from engine.registry import (  # noqa: F401  (default_config re-exported for callers)
    default_config,
    load_game,
)
from engine.rng import create_rng
from engine.types import GameConfig as EngineConfig
from engine.types import InstantGame

# ----------------------------------------------------------------------------- errors


class BetRejected(Exception):
    """Base: the bet was refused BEFORE any ledger movement."""


class StakeOutOfRange(BetRejected):
    """Stake is outside the game's ``[min_bet, max_bet]`` (or no limit configured)."""


class GameDisabled(BetRejected):
    """The game has no enabled ``GameConfig`` row."""


class RgDenied(BetRejected):
    """The responsible-gaming gate blocked the bet."""


class ActiveRoundExists(BetRejected):
    """A stateful round is already open for this ``(user, game)`` (one-active-round guard)."""


# ----------------------------------------------------------------------------- view model


class Fairness(BaseModel):
    """The public commit-reveal triple shown with a bet (spec §2.2)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    server_seed_hash: str
    client_seed: str
    nonce: int


class BetObject(BaseModel):
    """The spec §2.2 bet object — the return value of the loop / the REST envelope."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    bet_id: str
    game_id: str
    user_id: str
    wallet_id: str
    currency: str
    mode: str
    stake_minor: int
    status: str
    fairness: Fairness
    input: dict[str, Any]
    outcome: dict[str, Any]
    created_at: datetime | None = None
    settled_at: datetime | None = None
    idempotency_keys: dict[str, str]


# ----------------------------------------------------------------------------- helpers


def _idempotency_key(bet_id: str, op: str) -> str:
    """``hash(betId, opType)`` — the ledger dedup key (single-debit / single-credit)."""
    return hashlib.sha256(f"{bet_id}:{op}".encode()).hexdigest()


def _is_instant(game: object) -> bool:
    """``InstantGame`` resolves in one ``play`` call; ``StatefulGame`` has ``init``/``step``."""
    return hasattr(game, "play")


@dataclass(frozen=True)
class _Fairness:
    server_seed_id: str
    server_seed: bytes
    server_seed_hash: str
    client_seed: str
    nonce: int


async def _latest_enabled_config(
    session: AsyncSession, game_id: str
) -> GameConfigRow | None:
    """The authoritative runtime config: the highest enabled version for the game."""
    row: GameConfigRow | None = await session.scalar(
        select(GameConfigRow)
        .where(GameConfigRow.game_id == game_id, GameConfigRow.enabled.is_(True))
        .order_by(GameConfigRow.version.desc())
        .limit(1)
    )
    return row


async def _reserve_fairness(session: AsyncSession, user_id: str) -> _Fairness:
    """Provision (lazily) the user's active server/client seed and reserve the next
    monotonic nonce under a ``FOR UPDATE`` lock on the counter.

    Must run INSIDE the persisting transaction so a losing concurrent attempt's
    nonce bump rolls back with its failed ``betId`` insert. Seed *generation* uses
    the app-side CSPRNG (``secrets``) — never the engine. (S7 owns full seed
    lifecycle/encryption + the provisioning race; here ``seed_encrypted`` is a
    plain-hex placeholder.)
    """
    seed = await session.scalar(
        select(ServerSeed)
        .where(ServerSeed.user_id == user_id, ServerSeed.rotated_at.is_(None))
        .order_by(ServerSeed.id)
        .limit(1)
    )
    if seed is None:
        raw = secrets.token_bytes(32)
        seed = ServerSeed(
            id=uuid4().hex,
            user_id=user_id,
            seed_hash=hashlib.sha256(raw).hexdigest(),
            seed_encrypted=raw.hex(),
        )
        session.add(seed)
        await session.flush()

    client = await session.get(ClientSeed, user_id)
    if client is None:
        client = ClientSeed(user_id=user_id, value=secrets.token_hex(8))
        session.add(client)
        await session.flush()

    counter = await session.get(
        NonceCounter, (user_id, seed.id), with_for_update=True
    )
    if counter is None:
        # First-ever bet for this (user, seed): create the counter. Two of a
        # user's very first bets racing here collide on this PK — that
        # seed-provisioning race is S7's territory (the loop's per-betId race is
        # handled at the persist step); an existing counter serialises via the
        # FOR UPDATE lock above.
        counter = NonceCounter(user_id=user_id, server_seed_id=seed.id, value=0)
        session.add(counter)
        await session.flush()
    nonce = counter.value
    counter.value = nonce + 1

    return _Fairness(
        server_seed_id=seed.id,
        server_seed=bytes.fromhex(seed.seed_encrypted),
        server_seed_hash=seed.seed_hash,
        client_seed=client.value,
        nonce=nonce,
    )


def _bet_object_from_audit(bet: Bet, payload: dict[str, Any]) -> BetObject:
    """Rebuild the §2.2 object from the immutable audit record (the canonical
    fairness + outcome snapshot) — used for an idempotent replay / concurrent loser."""
    return BetObject(
        bet_id=bet.id,
        game_id=str(payload["gameId"]),
        user_id=bet.user_id,
        wallet_id=bet.wallet_id,
        currency=bet.currency,
        mode=bet.mode,
        stake_minor=bet.stake_minor,
        status=bet.status,
        fairness=Fairness(
            server_seed_hash=str(payload["serverSeedHash"]),
            client_seed=str(payload["clientSeed"]),
            nonce=int(payload["nonce"]),
        ),
        input=dict(payload.get("input") or {}),
        outcome=dict(payload.get("outcome") or {}),
        created_at=bet.created_at,
        settled_at=bet.settled_at,
        idempotency_keys={
            "debit": bet.idempotency_key_debit or "",
            "credit": bet.idempotency_key_credit or "",
        },
    )


async def _finalize_existing(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger, bet_id: str
) -> BetObject:
    """Return the ORIGINAL bet for a known ``betId``; re-issue the idempotent win
    credit if a WON bet's credit never landed (crash-between-decided-and-credited)."""
    async with session_factory() as session:
        bet = await session.get(Bet, bet_id)
        if bet is None:  # pragma: no cover - only reached if the row truly exists
            raise BetRejected(f"bet {bet_id} vanished")
        audit = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.bet_id == bet_id, AuditEvent.type == "BET_SETTLED")
            .limit(1)
        )
    payload = dict(audit.payload or {}) if audit is not None else {}
    if bet.status == "WON" and bet.payout_minor and bet.idempotency_key_credit:
        await ledger.credit(
            wallet_id=bet.wallet_id,
            amount_minor=bet.payout_minor,
            idempotency_key=bet.idempotency_key_credit,
            ref=bet.id,
        )
    return _bet_object_from_audit(bet, payload)


async def assert_no_active_round(
    session_factory: async_sessionmaker[AsyncSession], user_id: str, game_id: str
) -> None:
    """The one-active-(user, game)-round guard, OWNED by the shared loop.

    Stateful games (Mines S12, HiLo S14, Blackjack S25, …) call this from their
    ``init`` path before opening a round; they never re-implement it. Instant
    games settle atomically (no ACTIVE phase) and are not gated — concurrent
    instant bets are allowed.
    """
    async with session_factory() as session:
        active = await session.scalar(
            select(GameRound.id)
            .join(Bet, Bet.round_id == GameRound.id)
            .where(
                Bet.user_id == user_id,
                GameRound.game_id == game_id,
                GameRound.status == "ACTIVE",
            )
            .limit(1)
        )
    if active is not None:
        raise ActiveRoundExists(f"user {user_id} already has an active {game_id} round")


# ----------------------------------------------------------------------------- entry point


async def place_bet(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    user_id: str,
    game_id: str,
    bet_id: str,
    stake_minor: int,
    currency: str = "GOLD",
    mode: str = "PLAY",
    input: dict[str, Any] | None = None,
) -> BetObject:
    """Place + settle one instant bet, server-authoritative and idempotent on ``bet_id``."""
    bet_input: dict[str, Any] = dict(input or {})
    if stake_minor <= 0:
        raise StakeOutOfRange(f"stake {stake_minor} must be positive")

    game = load_game(game_id)  # raises engine.registry.UnknownGame for an unknown id
    if not _is_instant(game):  # stateful path lands in S12 (this loop owns the guard)
        raise NotImplementedError(f"stateful game {game_id} not wired until S12")

    # 1. Validate against the AUTHORITATIVE DB config + limits (no money moves yet).
    async with session_factory() as session:
        cfg_row = await _latest_enabled_config(session, game_id)
        if cfg_row is None:
            raise GameDisabled(f"no enabled config for {game_id}")
        limit = await session.get(GameLimit, (game_id, currency))
        if limit is None:
            raise StakeOutOfRange(f"no limit for {game_id}/{currency}")
        if not (limit.min_bet <= stake_minor <= limit.max_bet):
            raise StakeOutOfRange(
                f"stake {stake_minor} outside [{limit.min_bet}, {limit.max_bet}]"
            )
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
        config_version = cfg_row.version
        max_win = limit.max_win
        engine_cfg = EngineConfig(edge=float(cfg_row.edge), params=dict(cfg_row.params or {}))
        existing = await session.get(Bet, bet_id)

    # 2. Idempotent replay: a known bet returns the original (heals an un-credited win).
    if existing is not None:
        return await _finalize_existing(session_factory, ledger, bet_id)

    # 3. RG gate BEFORE the debit — a deny moves no credits.
    decision = can_bet(
        user_id=user_id, game_id=game_id, stake_minor=stake_minor, currency=currency
    )
    if not decision.allowed:
        raise RgDenied(decision.reason or "blocked by responsible-gaming policy")

    key_debit = _idempotency_key(bet_id, "WAGER")
    key_credit = _idempotency_key(bet_id, "WIN")

    # 4. Single debit (InsufficientFunds aborts here — nothing persisted/stranded).
    await ledger.debit(
        wallet_id=wallet_id, amount_minor=stake_minor, idempotency_key=key_debit, ref=bet_id
    )

    # 5. Reserve nonce + derive + persist — ONE transaction (loser's nonce rolls back).
    now = datetime.now(UTC)
    try:
        async with session_factory() as session, session.begin():
            fair = await _reserve_fairness(session, user_id)
            rng = create_rng(fair.server_seed, fair.client_seed, fair.nonce)
            outcome = cast(InstantGame, game).play(bet_input, rng, engine_cfg)
            payout = cap(apply_multiplier(stake_minor, outcome.multiplier), max_win)
            won = payout > 0
            status = "WON" if won else "LOST"
            outcome_dict: dict[str, Any] = {
                **outcome.detail,
                "multiplier": outcome.multiplier,
                "payoutMinor": payout,
            }
            audit_payload: dict[str, Any] = {
                "gameId": game_id,
                "serverSeedId": fair.server_seed_id,
                "serverSeedHash": fair.server_seed_hash,
                "clientSeed": fair.client_seed,
                "nonce": fair.nonce,
                "input": bet_input,
                "configVersion": config_version,
                "outcome": outcome_dict,
            }
            # Flush in FK order (round → bet → audit): these tables have no ORM
            # relationships, so the unit-of-work does not auto-order the inserts.
            session.add(
                GameRound(
                    id=bet_id,
                    game_id=game_id,
                    type="SINGLE",
                    server_seed_id=fair.server_seed_id,
                    nonce=fair.nonce,
                    status="SETTLED",
                    input=bet_input,
                    outcome=outcome_dict,
                    config_version=config_version,
                    created_at=now,
                    settled_at=now,
                )
            )
            await session.flush()
            session.add(
                Bet(
                    id=bet_id,
                    round_id=bet_id,
                    user_id=user_id,
                    wallet_id=wallet_id,
                    currency=currency,
                    mode=mode,
                    stake_minor=stake_minor,
                    status=status,
                    multiplier=Decimal(str(outcome.multiplier)),
                    payout_minor=payout,
                    idempotency_key_debit=key_debit,
                    idempotency_key_credit=key_credit,
                    created_at=now,
                    settled_at=now,
                )
            )
            await session.flush()
            session.add(
                AuditEvent(
                    id=uuid4().hex,
                    bet_id=bet_id,
                    round_id=bet_id,
                    type="BET_SETTLED",
                    payload=audit_payload,
                )
            )
    except IntegrityError:
        # Distinguish the legitimate race from a genuine constraint failure: only a
        # concurrent duplicate that won the betId PK race leaves a committed bet
        # behind (its own nonce bump rolled back with this failed insert). If no
        # bet exists, this was some OTHER integrity error — surface it rather than
        # masking it as a phantom "duplicate" (the debit already committed, so a
        # buried error must be visible, not swallowed).
        async with session_factory() as session:
            winner = await session.get(Bet, bet_id)
        if winner is None:
            raise
        return await _finalize_existing(session_factory, ledger, bet_id)

    # 6. Single credit on a win (idempotent; matches the just-committed bet).
    if won:
        await ledger.credit(
            wallet_id=wallet_id, amount_minor=payout, idempotency_key=key_credit, ref=bet_id
        )

    return BetObject(
        bet_id=bet_id,
        game_id=game_id,
        user_id=user_id,
        wallet_id=wallet_id,
        currency=currency,
        mode=mode,
        stake_minor=stake_minor,
        status=status,
        fairness=Fairness(
            server_seed_hash=fair.server_seed_hash,
            client_seed=fair.client_seed,
            nonce=fair.nonce,
        ),
        input=bet_input,
        outcome=outcome_dict,
        created_at=now,
        settled_at=now,
        idempotency_keys={"debit": key_debit, "credit": key_credit},
    )
