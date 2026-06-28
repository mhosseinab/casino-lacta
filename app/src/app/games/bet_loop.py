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
from engine.rng import HmacRngStream, create_rng
from engine.types import GameConfig as EngineConfig
from engine.types import InstantGame, StatefulGame

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


class RoundNotFound(Exception):
    """No round for the given ``(round_id, game_id)`` owned by the caller (or it's
    another user's round — same error, so existence is not disclosed)."""


class RoundTerminal(Exception):
    """An action was attempted on a terminal round (LOST / CASHED_OUT / SETTLED)."""


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
    """Place a bet, server-authoritative and idempotent on ``bet_id``.

    Instant games settle atomically (debit → outcome → credit). Stateful games
    (Mines S12, …) OPEN a round here — single debit + committed layout, status
    ACTIVE — and resolve later via :func:`step_action`; the credit happens at cashout.
    """
    bet_input: dict[str, Any] = dict(input or {})
    if stake_minor <= 0:
        raise StakeOutOfRange(f"stake {stake_minor} must be positive")

    game = load_game(game_id)  # raises engine.registry.UnknownGame for an unknown id

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

    # 1b. Per-game input fence — pure, at the boundary BEFORE any nonce reservation,
    # debit, or init/play(). An out-of-range/malformed input raises InvalidBetInput
    # and moves ZERO credits. Both InstantGame and StatefulGame conform; same engine
    # seam the verifier sees; the router maps the raised error to a 4xx.
    instant = _is_instant(game)
    game.validate_input(bet_input, engine_cfg)

    # 2. Idempotent replay: a known bet returns the original (instant heals an
    # un-credited win; stateful returns the round's current safe projection).
    if existing is not None:
        if instant:
            return await _finalize_existing(session_factory, ledger, bet_id)
        return await _stateful_replay(session_factory, bet_id, game_id)

    # 3. RG gate BEFORE the debit — a deny moves no credits.
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id=game_id,
        stake_minor=stake_minor,
        currency=currency,
    )
    if not decision.allowed:
        raise RgDenied(str(decision.reason) if decision.reason else "RG_BLOCKED")

    # 3b. Stateful: open a server-held round (single debit, committed layout, ACTIVE).
    # The shared one-active-(user,game)-round guard runs BEFORE any money moves. It is
    # best-effort (advisory, like the S6 guard): it runs in its own session with no
    # lock, so two simultaneous distinct-betId opens could both pass before either
    # round is ACTIVE. S12 only requires blocking a SECOND (sequential) round; a hard
    # concurrency constraint is out of scope here.
    if not instant:
        await assert_no_active_round(session_factory, user_id, game_id)
        return await _open_stateful_round(
            session_factory,
            ledger,
            game=cast("StatefulGame", game),
            user_id=user_id,
            game_id=game_id,
            bet_id=bet_id,
            stake_minor=stake_minor,
            currency=currency,
            mode=mode,
            wallet_id=wallet_id,
            config_version=config_version,
            engine_cfg=engine_cfg,
            bet_input=bet_input,
        )

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


# --------------------------------------------------------------------- stateful saga


async def _open_stateful_round(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    game: StatefulGame,
    user_id: str,
    game_id: str,
    bet_id: str,
    stake_minor: int,
    currency: str,
    mode: str,
    wallet_id: str,
    config_version: int,
    engine_cfg: EngineConfig,
    bet_input: dict[str, Any],
) -> BetObject:
    """Open a server-held round: single debit at start, commit the hidden layout from
    the seeded stream, persist ``status=ACTIVE``. NO credit here — settlement is
    ``step_action`` (cashout). The full opaque state goes to the server-only
    ``server_state`` column; the client-facing ``outcome`` is the GAME's own
    ``public_view`` snapshot (HiLo surfaces its opening shown card; Mines surfaces no
    unrevealed cell)."""
    key_debit = _idempotency_key(bet_id, "WAGER")
    key_credit = _idempotency_key(bet_id, "WIN")

    # Single debit at open (InsufficientFunds aborts here — nothing persisted).
    await ledger.debit(
        wallet_id=wallet_id, amount_minor=stake_minor, idempotency_key=key_debit, ref=bet_id
    )

    now = datetime.now(UTC)
    open_projection: dict[str, Any] = {}
    try:
        async with session_factory() as session, session.begin():
            fair = await _reserve_fairness(session, user_id)
            rng = create_rng(fair.server_seed, fair.client_seed, fair.nonce)
            game_state = game.init(bet_input, rng, engine_cfg)  # opaque; not inspected here
            # The client-facing OPEN snapshot is the GAME's own redaction — the saga
            # serializes its return and never indexes a key inside the opaque state.
            open_projection = game.public_view(game_state)
            # The SAGA-level fairness envelope: ``cursor`` is how far the seeded stream
            # has advanced (init's committed draws); ``game`` is the OPAQUE per-game
            # state. The app reads only ``cursor``/``game`` — never a key inside ``game``
            # — and resumes the stream at ``cursor`` for each later step (HiLo draws a
            # fresh card per guess; Mines committed everything here so its cursor is M).
            server_state = {"cursor": rng.cursor, "game": game_state}
            session.add(
                GameRound(
                    id=bet_id,
                    game_id=game_id,
                    type="SINGLE",
                    server_seed_id=fair.server_seed_id,
                    nonce=fair.nonce,
                    status="ACTIVE",
                    input=bet_input,
                    outcome=open_projection,
                    server_state=server_state,  # SERVER-ONLY (redaction); {cursor, game}
                    config_version=config_version,
                    created_at=now,
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
                    round_id=bet_id,
                    type="ROUND_OPENED",
                    payload={
                        "gameId": game_id,
                        "serverSeedId": fair.server_seed_id,
                        "serverSeedHash": fair.server_seed_hash,
                        "clientSeed": fair.client_seed,
                        "nonce": fair.nonce,
                        "input": bet_input,
                        "configVersion": config_version,
                    },
                )
            )
    except IntegrityError:
        # A concurrent open won the betId PK race (its nonce bump rolled back with
        # this failed insert). Return its current round; a non-duplicate error surfaces.
        async with session_factory() as session:
            winner = await session.get(Bet, bet_id)
        if winner is None:
            raise
        return await _stateful_replay(session_factory, bet_id, game_id)

    return BetObject(
        bet_id=bet_id,
        game_id=game_id,
        user_id=user_id,
        wallet_id=wallet_id,
        currency=currency,
        mode=mode,
        stake_minor=stake_minor,
        status="ACTIVE",
        fairness=Fairness(
            server_seed_hash=fair.server_seed_hash,
            client_seed=fair.client_seed,
            nonce=fair.nonce,
        ),
        input=bet_input,
        outcome=open_projection,
        created_at=now,
        settled_at=None,
        idempotency_keys={"debit": key_debit, "credit": key_credit},
    )


async def _stateful_replay(
    session_factory: async_sessionmaker[AsyncSession], bet_id: str, game_id: str
) -> BetObject:
    """An idempotent /bet resend returns the round's CURRENT safe projection (status +
    layout-free outcome), moving no money. Fairness fields come from the immutable
    ROUND_OPENED audit record."""
    async with session_factory() as session:
        bet = await session.get(Bet, bet_id)
        round_row = await session.get(GameRound, bet_id)
        audit = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.bet_id == bet_id, AuditEvent.type == "ROUND_OPENED")
            .limit(1)
        )
    if bet is None or round_row is None:
        raise RoundNotFound(f"no round for bet {bet_id}")
    payload = dict(audit.payload or {}) if audit is not None else {}
    return BetObject(
        bet_id=bet.id,
        game_id=game_id,
        user_id=bet.user_id,
        wallet_id=bet.wallet_id,
        currency=bet.currency,
        mode=bet.mode,
        stake_minor=bet.stake_minor,
        status=bet.status,
        fairness=Fairness(
            server_seed_hash=str(payload.get("serverSeedHash", "")),
            client_seed=str(payload.get("clientSeed", "")),
            nonce=int(payload.get("nonce", round_row.nonce or 0)),
        ),
        input=dict(round_row.input or {}),
        outcome=dict(round_row.outcome or {}),
        created_at=bet.created_at,
        settled_at=bet.settled_at,
        idempotency_keys={
            "debit": bet.idempotency_key_debit or "",
            "credit": bet.idempotency_key_credit or "",
        },
    )


async def _resume_rng(
    session: AsyncSession, round_row: GameRound, user_id: str, cursor: int
) -> HmacRngStream:
    """Reconstruct the round's seeded stream AT ``cursor`` for the next step.

    The stream is counter-indexed (``HMAC(serverSeed, "{clientSeed}:{nonce}:{cursor}")``),
    so a mid-round position is fully recoverable from ``(serverSeed, clientSeed, nonce,
    cursor)`` — no secret is ever stashed in the redactable round state. The unrevealed
    server seed stays in ``server_seeds`` (the provable-fairness commitment); we read it
    here only to advance the authoritative server-side stream."""
    server_seed = await session.get(ServerSeed, round_row.server_seed_id)
    client = await session.get(ClientSeed, user_id)
    if server_seed is None or client is None:  # pragma: no cover - opened round always has both
        raise RoundNotFound(f"missing fairness material for round {round_row.id}")
    return HmacRngStream(
        server_seed=bytes.fromhex(server_seed.seed_encrypted),
        client_seed=client.value,
        nonce=round_row.nonce or 0,
        cursor=cursor,
    )


def stateful_round_view(game: StatefulGame, server_state: dict[str, Any] | None) -> dict[str, Any]:
    """The client-safe snapshot of a stateful round (for ``/state`` resume), via the
    GAME's own ``public_view``. Unwraps the ``{cursor, game}`` envelope and hands the
    opaque game state to the game — the saga never indexes a key inside ``game``."""
    envelope = dict(server_state or {})
    return game.public_view(dict(envelope.get("game") or {}))


async def step_action(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    user_id: str,
    game_id: str,
    round_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Advance a stateful round by one action (reveal / cashout), server-authoritative.

    Action serialization: the ``GameRound`` row is locked ``FOR UPDATE`` for the whole
    transition, so concurrent reveals serialize and cannot double-advance ``k``. The
    app never inspects the opaque ``server_state`` — it feeds it to ``GAME.step`` and
    drives credit/round-status from the generic ``Outcome.detail["status"]`` and
    ``Outcome.multiplier``. The credit (cashout only) is single + idempotent and runs
    AFTER the locked commit, self-healing a crash between the committed state and it."""
    game = load_game(game_id)
    if _is_instant(game):
        raise RoundTerminal(f"{game_id} has no stateful actions")
    stateful = cast("StatefulGame", game)

    now = datetime.now(UTC)
    credit_req: tuple[str, int, str] | None = None  # (wallet_id, amount, key_credit)
    response: dict[str, Any]

    async with session_factory() as session, session.begin():
        # Row lock for the whole transition — serializes concurrent actions.
        round_row = await session.get(GameRound, round_id, with_for_update=True)
        if round_row is None or round_row.game_id != game_id:
            raise RoundNotFound(f"no {game_id} round {round_id}")
        bet = await session.scalar(select(Bet).where(Bet.round_id == round_id))
        if bet is None or bet.user_id != user_id:
            # Identity fence: never disclose or act on another user's round.
            raise RoundNotFound(f"no {game_id} round {round_id}")

        op = action.get("op")
        status = round_row.status

        if status != "ACTIVE":
            # Terminal: honour ONLY an idempotent cashout replay (heal an un-landed
            # credit); reject every other action on a finished round.
            if op == "cashout" and status == "CASHED_OUT":
                response = dict(round_row.outcome or {})
                response["roundId"] = round_id
                response["payoutMinor"] = bet.payout_minor or 0
                if bet.payout_minor and bet.idempotency_key_credit:
                    credit_req = (bet.wallet_id, bet.payout_minor, bet.idempotency_key_credit)
            else:
                raise RoundTerminal(f"round {round_id} is {status}; no further actions")
        else:
            # The saga-level fairness envelope: {cursor, game}. Reconstruct the seeded
            # stream at the persisted cursor (counter-indexed, resumable purely from
            # (serverSeed, clientSeed, nonce, cursor)) so a game that draws fresh
            # entropy per action (HiLo's next card) continues the SAME stream the
            # verifier replays; a game whose entropy is committed at init (Mines)
            # ignores it and leaves the cursor unchanged.
            envelope = dict(round_row.server_state or {})
            stored_cursor = int(envelope.get("cursor", 0))
            game_state = dict(envelope.get("game") or {})  # OPAQUE — no key is read here
            rng = await _resume_rng(session, round_row, bet.user_id, stored_cursor)
            next_game, outcome = stateful.step(
                game_state, action, rng
            )  # may raise InvalidBetInput
            if outcome is None:  # pragma: no cover - Mines/HiLo yield a projection per action
                raise RoundTerminal(f"round {round_id} produced no outcome")
            # Two intentionally distinct client projections: ``Outcome.detail`` is the
            # per-TRANSITION event (what JUST happened — revealedRank/won/stepMultiplier,
            # cell/safe), returned by /action; ``public_view`` is the round STATE SNAPSHOT
            # (where the round IS now), returned by /bet-open and /state. They overlap on
            # status but answer different questions, so they are not unified — both remain
            # the GAME's own redaction (no app-side key reads), so neither can leak.
            detail = dict(outcome.detail)
            new_status = str(detail["status"])

            # Persist the advanced envelope: the new opaque game state + the cursor the
            # stream reached (HiLo: +1 per guess; Mines: unchanged).
            round_row.server_state = {"cursor": rng.cursor, "game": next_game}
            round_row.outcome = detail  # client-facing transition projection (no leak when ACTIVE)
            response = dict(detail)
            response["roundId"] = round_id

            if new_status == "CASHED_OUT":
                limit = await session.get(GameLimit, (game_id, bet.currency))
                max_win = limit.max_win if limit is not None else 0
                payout = cap(apply_multiplier(bet.stake_minor, outcome.multiplier), max_win)
                round_row.status = "CASHED_OUT"
                round_row.settled_at = now
                bet.status = "WON"
                bet.payout_minor = payout
                bet.multiplier = Decimal(str(outcome.multiplier))
                bet.settled_at = now
                response["payoutMinor"] = payout
                key_credit = bet.idempotency_key_credit or _idempotency_key(round_id, "WIN")
                credit_req = (bet.wallet_id, payout, key_credit)
            elif new_status == "LOST":
                round_row.status = "LOST"
                round_row.settled_at = now
                bet.status = "LOST"
                bet.payout_minor = 0
                bet.multiplier = Decimal("0")
                bet.settled_at = now
            # ACTIVE: a safe reveal — round continues, nothing settled.

    # Single credit on cashout (idempotent; outside the round lock).
    if credit_req is not None:
        wallet_id, amount, key_credit = credit_req
        if amount > 0:
            await ledger.credit(
                wallet_id=wallet_id, amount_minor=amount, idempotency_key=key_credit, ref=round_id
            )

    return response
