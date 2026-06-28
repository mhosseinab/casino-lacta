"""Crash async shell — the authoritative round actor, pub/sub seam, and WS gateway.

This is the thin async layer over the pure ``crash_core``: it drives the timed
state machine, generates each round's CSPRNG seed (``secrets`` — app-side, NEVER
in ``engine``), and fans round/tick/crash events out over a pub/sub broker so
multiple app instances stay consistent. ONE authoritative actor owns the loop and
PUBLISHES; every app instance SUBSCRIBES and forwards to its own WS clients — the
clients never touch the actor. Importing this module starts NOTHING; the actor is
a separable singleton (``CrashActor.run_forever``) a single partition process runs.

Outcome integrity (the realtime-integrity + fairness invariants):
  * C is computed once, at round open, by the pure core from the committed seed —
    it is the server's source of truth. The cosmetic ``cosmetic_multiplier`` tick
    is a pure function of the tick index (wall-clock only PACES it via ``sleep``);
    it climbs toward C and stops below it, and NEVER feeds back into C.
  * Commit-before-reveal: the WAITING ``round`` event carries the seed *hash* and
    NEITHER the raw seed NOR C; the raw ``serverSeed`` is revealed only in the
    ``crash`` event, and it hashes (SHA-256) back to the committed hash.

S18 has NO betting/money/ledger and persists NOTHING (no bet to lose on restart).
S19 adds the placed bet + cashout + persistence on top of this same core/actor.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import redis.asyncio as redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.crash_bets import (
    CrashBetResult,
    manual_cashout,
    next_crash_round_number,
    open_crash_round,
    place_crash_bet,
    recover_crash,
    settle_crash_round,
)
from app.ws.crash_core import CrashRound, RoundStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.wallet import Ledger

# House edge for Crash. S19: the AUTHORITATIVE value now lives in the seeded
# ``originals.crash`` DB GameConfig (migration f6a7b8c9d0e1, edge=0.01); this
# constant is the actor's default when no config-driven edge is injected.
DEFAULT_CRASH_EDGE = 0.01

# Single partition → a single broadcast channel. Every app instance subscribes here.
CRASH_CHANNEL = "crash:originals"
CRASH_WS_PATH = "/games/originals.crash"

_SEED_BYTES = 32
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class CrashBettingClosed(RuntimeError):
    """A bet was attempted outside the WAITING window (server-authoritative gate)."""


class CrashNotRunning(RuntimeError):
    """A cash-out was attempted while the round was not RUNNING."""


# --------------------------------------------------------------------------- #
# Cosmetic curve (presentation only — never an input to C)
# --------------------------------------------------------------------------- #
def cosmetic_multiplier(tick_index: int, growth: float) -> float:
    """The displayed multiplier at a given tick — a pure function of the index.

    Two-decimal, monotonically rising from 1.00× at tick 0. This drives ONLY what
    the client renders and when the actor stops the RUNNING phase (cosmetic ≥ C);
    it has no bearing on C, which the core fixed at round open.
    """
    return math.floor(growth**tick_index * 100) / 100


# --------------------------------------------------------------------------- #
# Pub/sub seam (Redis in prod; in-memory for tests) — fan-out to N subscribers
# --------------------------------------------------------------------------- #
class SupportsPublish(Protocol):
    """The narrow capability the actor depends on (ISP): publish only."""

    async def publish(self, channel: str, message: str) -> None: ...


class CrashBroker(SupportsPublish, Protocol):
    """A full pub/sub broker: publish + subscribe + close (the WS gateway needs all)."""

    def subscribe(self, channel: str) -> Any:  # AbstractAsyncContextManager[AsyncIterator[str]]
        ...

    async def aclose(self) -> None: ...


class InMemoryPubSub:
    """An in-process broker for tests/local: per-subscriber queues, full fan-out.

    Each ``subscribe`` gets its OWN queue, so two subscribers (modelling two app
    instances / WS clients) each receive every published message — the property
    that makes routing through Redis meaningful. ``log`` records published
    (channel, message) pairs for test assertions.
    """

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}

    async def publish(self, channel: str, message: str) -> None:
        self.log.append((channel, message))
        for queue in self._subscribers.get(channel, []):
            queue.put_nowait(message)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[AsyncIterator[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.setdefault(channel, []).append(queue)

        async def _stream() -> AsyncIterator[str]:
            while True:
                yield await queue.get()

        try:
            yield _stream()
        finally:
            self._subscribers[channel].remove(queue)

    async def aclose(self) -> None:  # symmetry with RedisPubSub; nothing to release
        return None


class RedisPubSub:
    """A Redis-backed broker (the production fan-out across app instances)."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str = _REDIS_URL) -> RedisPubSub:
        return cls(redis.from_url(url))

    async def publish(self, channel: str, message: str) -> None:
        await self._client.publish(channel, message)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[AsyncIterator[str]]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)

        async def _stream() -> AsyncIterator[str]:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=None
                )
                if message is None:
                    continue
                data = message["data"]
                yield data.decode() if isinstance(data, bytes) else str(data)

        try:
            yield _stream()
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis stub gap

    async def aclose(self) -> None:
        await self._client.aclose()


# --------------------------------------------------------------------------- #
# Event builders — explicit per-status payloads (NEVER dump the whole round)
# --------------------------------------------------------------------------- #
def _round_event(rnd: CrashRound) -> dict[str, Any]:
    """WAITING: the commitment only — no raw seed, no C."""
    return {
        "type": "round",
        "roundId": rnd.round_id,
        "roundNumber": rnd.round_number,
        "serverSeedHash": rnd.server_seed_hash,
        "status": rnd.status.value,
    }


def _tick_event(rnd: CrashRound, multiplier: float) -> dict[str, Any]:
    """RUNNING: the cosmetic climbing multiplier — still no seed, no C."""
    return {
        "type": "tick",
        "roundId": rnd.round_id,
        "roundNumber": rnd.round_number,
        "multiplier": multiplier,
    }


def _crash_event(rnd: CrashRound) -> dict[str, Any]:
    """CRASHED: reveal the seed + C; the seed hashes back to the committed hash."""
    return {
        "type": "crash",
        "roundId": rnd.round_id,
        "roundNumber": rnd.round_number,
        "crashPoint": rnd.C,
        "serverSeed": rnd.round_server_seed.hex(),
        "serverSeedHash": rnd.server_seed_hash,
        "status": rnd.status.value,
    }


# --------------------------------------------------------------------------- #
# The authoritative round actor
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoundTimings:
    """Pacing for the cosmetic loop (wall-clock only — none of this touches C)."""

    waiting: float = 5.0  # commit/countdown window before the round runs
    tick_interval: float = 0.1  # gap between cosmetic ticks
    settle: float = 3.0  # post-crash pause before the next round
    growth: float = 1.06  # per-tick cosmetic growth factor


DEFAULT_TIMINGS = RoundTimings()


def _default_seed() -> bytes:
    """Fresh CSPRNG per-round server seed (app-side; never in engine)."""
    return secrets.token_bytes(_SEED_BYTES)


class CrashActor:
    """The ONE authoritative round actor: drives the loop and publishes events.

    It depends only on ``SupportsPublish`` (ISP). ``seed_factory``/``id_factory``/
    ``sleep`` are injectable so the loop is deterministic and instant under test.
    """

    def __init__(
        self,
        publisher: SupportsPublish,
        *,
        edge: float = DEFAULT_CRASH_EDGE,
        channel: str = CRASH_CHANNEL,
        timings: RoundTimings = DEFAULT_TIMINGS,
        seed_factory: Callable[[], bytes] = _default_seed,
        id_factory: Callable[[int], str] = lambda n: f"round-{n}",
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        self._publisher = publisher
        self._edge = edge
        self._channel = channel
        self._timings = timings
        self._seed_factory = seed_factory
        self._id_factory = id_factory
        self._sleep = sleep
        # S19 money path: when both are wired, the actor persists each round at open
        # and settles every bet at crash. Left None (S18 default), the actor is a
        # pure presentation broadcaster — no betting, no DB (keeps S18 tests intact).
        self._session_factory = session_factory
        self._ledger = ledger
        # The CURRENT round + its last-PUBLISHED cosmetic tick — the server's
        # authoritative state for placement (WAITING) and manual cash-out (RUNNING).
        # The cash-out multiplier is stamped from ``_current_multiplier`` SERVER-SIDE;
        # a client never supplies a multiplier.
        self._round: CrashRound | None = None
        self._current_multiplier: float = 1.0

    async def _publish(self, event: dict[str, Any]) -> None:
        await self._publisher.publish(self._channel, json.dumps(event))

    async def run_round(self, round_number: int) -> CrashRound:
        """Open, run, and crash ONE round; return the final (SETTLED) round value.

        Seed is generated here (app-side CSPRNG) and committed before the outcome
        is broadcast; C is fixed by the pure core at open. The cosmetic curve
        climbs by tick index and the round CRASHES the instant the displayed
        multiplier would reach C (an instant-bust round, C == 1.00, emits no ticks).
        """
        rnd = CrashRound.open(
            round_server_seed=self._seed_factory(),
            round_id=self._id_factory(round_number),
            round_number=round_number,
            edge=self._edge,
        )
        self._round = rnd
        self._current_multiplier = 1.0
        # S19: persist the round tuple at START (committed before the outcome is
        # broadcast) so placed bets can reference it and settlement re-derives C.
        if self._persists:
            await open_crash_round(self._session_factory, rnd)  # type: ignore[arg-type]
        # WAITING — broadcast the commitment (hash + roundId), reveal NOTHING else.
        # Bets are placed in this window via ``place_bet`` (server-authoritative).
        await self._publish(_round_event(rnd))
        await self._sleep(self._timings.waiting)

        rnd = rnd.lock().start()  # LOCKED → RUNNING (betting window closed)
        self._round = rnd

        tick = 0
        while True:
            multiplier = cosmetic_multiplier(tick, self._timings.growth)
            if multiplier >= rnd.C:  # the curve reached C → crash now
                break
            # Stamp the authoritative current value BEFORE publishing: a manual
            # cash-out that lands now is settled at this last-published tick.
            self._current_multiplier = multiplier
            await self._publish(_tick_event(rnd, multiplier))
            await self._sleep(self._timings.tick_interval)
            tick += 1

        rnd = rnd.crash()  # CRASHED — reveal the seed (it hashes to the commitment)
        self._round = rnd
        # S19: settle every still-ACTIVE bet against C (auto-cashout wins iff t ≤ C;
        # everything else loses). Idempotent per bet — a re-settle is a pure replay.
        if self._persists:
            await settle_crash_round(self._session_factory, self._ledger, round_id=rnd.round_id)  # type: ignore[arg-type]
        await self._publish(_crash_event(rnd))
        await self._sleep(self._timings.settle)

        settled = rnd.settle()
        self._round = settled
        return settled

    @property
    def _persists(self) -> bool:
        """The money path is active only when BOTH the DB and ledger are wired."""
        return self._session_factory is not None and self._ledger is not None

    async def place_bet(
        self,
        *,
        user_id: str,
        bet_id: str,
        stake_minor: int,
        currency: str = "GOLD",
        mode: str = "PLAY",
        auto_cashout: float | None = None,
    ) -> CrashBetResult:
        """Place a bet on the CURRENT round (only during WAITING). Server-authoritative,
        single-debit, idempotent on ``bet_id``."""
        if not self._persists:
            raise RuntimeError("crash betting requires a session_factory + ledger")
        if self._round is None or self._round.status is not RoundStatus.WAITING:
            raise CrashBettingClosed("betting is only open during WAITING")
        return await place_crash_bet(
            self._session_factory,  # type: ignore[arg-type]
            self._ledger,  # type: ignore[arg-type]
            round_id=self._round.round_id,
            user_id=user_id,
            bet_id=bet_id,
            stake_minor=stake_minor,
            currency=currency,
            mode=mode,
            auto_cashout=auto_cashout,
        )

    async def cash_out(self, *, bet_id: str) -> CrashBetResult:
        """Manually cash out a bet on the CURRENT (RUNNING) round. The multiplier is
        the SERVER's last-published tick — the client sends ONLY ``bet_id``."""
        if not self._persists:
            raise RuntimeError("crash betting requires a session_factory + ledger")
        if self._round is None or self._round.status is not RoundStatus.RUNNING:
            raise CrashNotRunning("cash-out is only accepted while RUNNING")
        return await manual_cashout(
            self._session_factory,  # type: ignore[arg-type]
            self._ledger,  # type: ignore[arg-type]
            round_id=self._round.round_id,
            bet_id=bet_id,
            stamped_multiplier=self._current_multiplier,
        )

    @property
    def current_cosmetic_multiplier(self) -> float:
        """The actor's last-published cosmetic tick — the public value REST /state
        surfaces as the live multiplier (it is never an input to ``C``)."""
        return self._current_multiplier

    async def recover(self) -> int:
        """Boot recovery (the restart entrypoint): re-settle every orphaned round
        deterministically (the committed seed already fixed ``C``) and return the
        next round number recovered from the DB — so the actor NEVER regenerates an
        existing ``round-{n}`` id (the stale-seed collision hazard). A no-op for the
        S18 presentation-only actor (no DB) → starts at round 1."""
        if not self._persists:
            return 1
        await recover_crash(self._session_factory, self._ledger)  # type: ignore[arg-type]
        return await next_crash_round_number(self._session_factory)  # type: ignore[arg-type]

    async def run_forever(self) -> None:
        """Run rounds back-to-back forever (the singleton partition entrypoint).

        Not started on import: a dedicated process calls this so importing the app
        (and its WS router) never spins a crash loop. On entry it RECOVERS — orphaned
        rounds re-settle and the round counter continues from the DB (no id reuse).
        """
        round_number = (await self.recover()) - 1
        while True:
            round_number += 1
            await self.run_round(round_number)


# --------------------------------------------------------------------------- #
# WS gateway — subscribes to the broadcast channel and fans out to its clients
# --------------------------------------------------------------------------- #
async def _pump(
    broker: CrashBroker,
    channel: str,
    send: Callable[[str], Awaitable[None]],
) -> None:
    """Forward every message on ``channel`` to ``send`` (the WS client sink)."""
    async with broker.subscribe(channel) as stream:
        async for message in stream:
            await send(message)


def _get_broker() -> CrashBroker:
    """The broker the WS gateway subscribes through (Redis in prod)."""
    return RedisPubSub.from_url(_REDIS_URL)


router = APIRouter()


@router.websocket(CRASH_WS_PATH)
async def crash_ws(websocket: WebSocket) -> None:
    """Stream the authoritative round/tick/crash events to one client.

    Read-only fan-out: the client receives server-authoritative state and sends
    nothing that influences an outcome (there are no bets in S18).
    """
    await websocket.accept()
    broker = _get_broker()
    try:
        await _pump(broker, CRASH_CHANNEL, websocket.send_text)
    except WebSocketDisconnect:
        pass
    finally:
        with suppress(Exception):
            await broker.aclose()


__all__ = [
    "CRASH_CHANNEL",
    "CRASH_WS_PATH",
    "DEFAULT_CRASH_EDGE",
    "CrashActor",
    "CrashBettingClosed",
    "CrashBroker",
    "CrashNotRunning",
    "InMemoryPubSub",
    "RedisPubSub",
    "RoundTimings",
    "cosmetic_multiplier",
    "router",
]
