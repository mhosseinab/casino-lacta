"""Realtime poker gateway — one authoritative table actor + PER-SEAT redaction.

The thin async shell over the pure ``engine.poker.table`` reducer (S31): it owns ONE
authoritative ``TableState`` per table, applies validated player *intents* in turn order
(the server is the sole decider), holds the live state behind a state-store seam (Redis
in prod), checkpoints the deterministic replay tuple to Postgres so a table survives a
restart, and fans state out over the pub/sub broker — REUSING the Crash realtime seams
(``SupportsPublish`` / ``InMemoryPubSub`` / ``RedisPubSub`` / ``_pump``) verbatim, never
re-implementing the machinery.

★ THE LOAD-BEARING INVARIANT — per-seat redaction, leak-proof BY CONSTRUCTION. Redaction
happens at the SOURCE: the actor publishes a DIFFERENT, already-redacted view to each
seat's OWN channel via the engine whitelist ``engine.poker.table.public_view``. A seat's
hole cards (and the hidden ``community_undealt`` board-to-come) are NEVER serialized onto
another seat's channel — so even a compromised subscriber on channel X only ever sees
X's own holes plus public state. The full ``TableState`` never crosses the wire. This is
strictly stronger than redacting at the gateway (which would put every hole into the
broker first). Non-folded hands legitimately become public at a real showdown; a folded
hand is mucked and NEVER revealed (the projection enforces both).

Determinism / recovery (the S31 property reused): a hand is a pure function of the deal
seed + the action log. The checkpoint persists exactly ``{seed, blinds, startingStacks,
button, handNumber, actionLog}``; a fresh actor reconstructs the IDENTICAL state by
``deal_hand`` from the seed then replaying the log — the same shape Crash recovers from.

Money: NONE moves here. Buy-in / rake / the ledger are S33 — this step is the realtime
transport + redaction only. Seed *generation* (CSPRNG via ``secrets``) is app-side, never
in ``engine``.

WS auth posture: a connection is bound to the seat in its path (``…/seats/{seat_id}``) —
the SAME stubbed posture as Crash's raw-frame fan-out (real per-seat token auth is the
auth seam, layered later). What S32 proves is the integrity property independent of that:
the server never PUBLISHES seat X's holes on any channel other than X's, so even given
the connection→seat binding the redaction holds.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Protocol

import redis.asyncio as redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# Reuse the Crash realtime seams verbatim (DRY — one pub/sub machinery, one forward loop).
from app.ws.crash import (
    CrashBroker as Broker,  # the publish+subscribe+close broker protocol (transport-agnostic)
)
from app.ws.crash import (
    InMemoryPubSub,
    RedisPubSub,
    SupportsPublish,
    _pump,
)
from engine.poker.table import (
    Action,
    ActionKind,
    IllegalAction,
    TableConfig,
    TableState,
    apply_action,
    deal_hand,
    public_view,
)
from engine.rng import create_rng

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

POKER_GAME_ID = "poker.nlhe"
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_SEED_BYTES = 32

# The client-safe table view projection — the ENGINE whitelist. The actor depends on the
# abstraction (a pure ``(state, viewer) -> dict``) so the redaction lives in ONE audited
# place; prod always uses ``public_view`` (the only leak-proof projection).
ViewProjection = Callable[[TableState, int | None], dict[str, Any]]


# --------------------------------------------------------------------------- #
# Channel namespacing — one channel PER SEAT (redaction at the source).         #
# --------------------------------------------------------------------------- #
def seat_channel(table_id: str, seat_id: int) -> str:
    """The pub/sub channel carrying ONLY seat ``seat_id``'s redacted view."""
    return f"poker:{table_id}:seat:{seat_id}"


def public_channel(table_id: str) -> str:
    """The spectator channel — public state, no hole cards (until a real showdown)."""
    return f"poker:{table_id}:public"


# --------------------------------------------------------------------------- #
# State-store seam — Redis live, Postgres durable, in-memory for tests.         #
# --------------------------------------------------------------------------- #
class PokerStateStore(Protocol):
    """Persist + restore a table's deterministic replay tuple (the checkpoint).

    Narrow, consumer-shaped (ISP): the actor only ever saves/loads a JSON-safe dict.
    Swapping Redis for Postgres (or the in-memory test double) is an adapter change.
    """

    async def save(self, table_id: str, checkpoint: dict[str, Any]) -> None: ...

    async def load(self, table_id: str) -> dict[str, Any] | None: ...


class InMemoryStateStore:
    """An in-process store for tests/local. Stores the JSON STRING (not the dict) so a
    non-serializable checkpoint fails here exactly as it would against Redis/Postgres."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def save(self, table_id: str, checkpoint: dict[str, Any]) -> None:
        self._data[table_id] = json.dumps(checkpoint)

    async def load(self, table_id: str) -> dict[str, Any] | None:
        raw = self._data.get(table_id)
        if raw is None:
            return None
        checkpoint: dict[str, Any] = json.loads(raw)
        return checkpoint


class RedisStateStore:
    """The live-state store (Redis) — the table's working state during play."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str = _REDIS_URL) -> RedisStateStore:
        return cls(redis.from_url(url))

    @staticmethod
    def _key(table_id: str) -> str:
        return f"poker:state:{table_id}"

    async def save(self, table_id: str, checkpoint: dict[str, Any]) -> None:
        await self._client.set(self._key(table_id), json.dumps(checkpoint))

    async def load(self, table_id: str) -> dict[str, Any] | None:
        raw = await self._client.get(self._key(table_id))
        if raw is None:
            return None
        checkpoint: dict[str, Any] = json.loads(
            raw.decode() if isinstance(raw, bytes) else raw
        )
        return checkpoint

    async def aclose(self) -> None:
        await self._client.aclose()


class PostgresStateStore:
    """The DURABLE checkpoint (Postgres ``poker_tables`` row) behind the live Redis state,
    so a table survives a full process restart. Reuses the existing JSONB columns — no
    migration. The replay tuple lives in ``seats``; ``stakes``/``hand_no`` mirror it for
    inspection. No money column is touched (buy-in/rake is S33)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, table_id: str, checkpoint: dict[str, Any]) -> None:
        from app.db.models import PokerTable

        async with self._session_factory() as session, session.begin():
            row = await session.get(PokerTable, table_id, with_for_update=True)
            stakes = {
                "smallBlind": checkpoint["smallBlind"],
                "bigBlind": checkpoint["bigBlind"],
            }
            hand_no = int(checkpoint["handNumber"])
            if row is None:
                session.add(
                    PokerTable(
                        id=table_id,
                        stakes=stakes,
                        seats=checkpoint,
                        state="ACTIVE",
                        hand_no=hand_no,
                    )
                )
            else:
                row.stakes = stakes
                row.seats = checkpoint
                row.hand_no = hand_no

    async def load(self, table_id: str) -> dict[str, Any] | None:
        from app.db.models import PokerTable

        async with self._session_factory() as session:
            row = await session.get(PokerTable, table_id)
            if row is None or row.seats is None:
                return None
            return dict(row.seats)


# --------------------------------------------------------------------------- #
# The authoritative table actor.                                               #
# --------------------------------------------------------------------------- #
def _default_seed() -> bytes:
    """Fresh CSPRNG per-hand deal seed (app-side; never in engine)."""
    return secrets.token_bytes(_SEED_BYTES)


class PokerActor:
    """The ONE authoritative actor for a table: owns the ``TableState``, validates and
    applies player intents via the pure reducer, checkpoints the replay tuple, and
    publishes a PER-SEAT redacted view after every transition.

    ``publisher``/``store``/``seed_factory``/``view_projection`` are injected (DIP): the
    default ``view_projection`` is the engine whitelist — the only leak-proof projection.
    """

    def __init__(
        self,
        table_id: str,
        config: TableConfig,
        publisher: SupportsPublish,
        store: PokerStateStore,
        *,
        seed_factory: Callable[[], bytes] = _default_seed,
        view_projection: ViewProjection = public_view,
    ) -> None:
        self._table_id = table_id
        self._config = config
        self._publisher = publisher
        self._store = store
        self._seed_factory = seed_factory
        self._project = view_projection
        # The deterministic replay tuple for the CURRENT hand.
        self._seed: bytes | None = None
        self._hand_number = 0
        self._starting_stacks: tuple[int, ...] = ()
        self._button = 0
        self._action_log: list[Action] = []
        self._state: TableState | None = None

    # -- properties ------------------------------------------------------- #
    @property
    def state(self) -> TableState:
        if self._state is None:
            raise RuntimeError("no hand in progress; call start_hand first")
        return self._state

    @property
    def table_id(self) -> str:
        return self._table_id

    # -- lifecycle -------------------------------------------------------- #
    async def start_hand(
        self,
        stacks: tuple[int, ...],
        button: int,
        *,
        hand_number: int | None = None,
    ) -> TableState:
        """Open a new hand: generate the deal seed (committed), deal from it, checkpoint,
        and publish each seat's redacted opening view."""
        seed = self._seed_factory()
        number = self._hand_number + 1 if hand_number is None else hand_number
        rng = create_rng(seed, client_seed=self._table_id, nonce=number)
        state = deal_hand(self._config, tuple(stacks), button, rng)
        self._seed = seed
        self._hand_number = number
        self._starting_stacks = tuple(stacks)
        self._button = button
        self._action_log = []
        self._state = state
        await self._checkpoint()
        await self._broadcast()
        return state

    async def act(self, *, seat: int, kind: str, amount: int = 0) -> TableState | None:
        """Apply one player INTENT. The server is authoritative: the reducer validates
        turn order + legality. An illegal/out-of-turn intent is rejected with an error
        sent ONLY to that seat's channel — the table is NOT mutated and nothing leaks.
        Returns the new state on success, ``None`` on rejection."""
        try:
            action_kind = ActionKind(kind)
        except ValueError:
            await self._send_error(seat, f"unknown action {kind!r}")
            return None
        action = Action(seat=seat, kind=action_kind, amount=amount)
        try:
            new_state = apply_action(self.state, action)
        except IllegalAction as exc:
            # Rejected before any state change (the reducer raises before mutating).
            await self._send_error(seat, str(exc))
            return None
        self._action_log.append(action)
        self._state = new_state
        await self._checkpoint()
        await self._broadcast()
        return new_state

    # -- recovery --------------------------------------------------------- #
    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: dict[str, Any],
        publisher: SupportsPublish,
        store: PokerStateStore,
        *,
        view_projection: ViewProjection = public_view,
    ) -> PokerActor:
        """Reconstruct an actor from a persisted checkpoint — the EXACT state, rebuilt by
        ``deal_hand`` from the committed seed then replaying the action log (the S31
        determinism property). Byte-identical to the live actor it resumes."""
        config = TableConfig(
            small_blind=int(checkpoint["smallBlind"]),
            big_blind=int(checkpoint["bigBlind"]),
        )
        actor = cls(
            checkpoint["tableId"],
            config,
            publisher,
            store,
            view_projection=view_projection,
        )
        actor._seed = bytes.fromhex(checkpoint["seed"])
        actor._hand_number = int(checkpoint["handNumber"])
        actor._starting_stacks = tuple(int(s) for s in checkpoint["startingStacks"])
        actor._button = int(checkpoint["button"])
        actor._action_log = [
            Action(seat=int(a["seat"]), kind=ActionKind(a["kind"]), amount=int(a["amount"]))
            for a in checkpoint["actionLog"]
        ]
        actor._state = actor._replay()
        return actor

    @classmethod
    async def reload(
        cls,
        table_id: str,
        publisher: SupportsPublish,
        store: PokerStateStore,
        *,
        view_projection: ViewProjection = public_view,
    ) -> PokerActor | None:
        """Load a table's checkpoint from the store and reconstruct its actor, or ``None``
        if no checkpoint exists. The restart entrypoint (mirrors Crash's recover)."""
        checkpoint = await store.load(table_id)
        if checkpoint is None:
            return None
        return cls.from_checkpoint(
            checkpoint, publisher, store, view_projection=view_projection
        )

    def _replay(self) -> TableState:
        assert self._seed is not None
        rng = create_rng(self._seed, client_seed=self._table_id, nonce=self._hand_number)
        state = deal_hand(self._config, self._starting_stacks, self._button, rng)
        for action in self._action_log:
            state = apply_action(state, action)
        return state

    # -- persistence + fan-out ------------------------------------------- #
    def _checkpoint_payload(self) -> dict[str, Any]:
        assert self._seed is not None
        return {
            "tableId": self._table_id,
            "seed": self._seed.hex(),
            "smallBlind": self._config.small_blind,
            "bigBlind": self._config.big_blind,
            "startingStacks": list(self._starting_stacks),
            "button": self._button,
            "handNumber": self._hand_number,
            "actionLog": [
                {"seat": a.seat, "kind": a.kind.value, "amount": a.amount}
                for a in self._action_log
            ],
        }

    async def _checkpoint(self) -> None:
        await self._store.save(self._table_id, self._checkpoint_payload())

    async def _broadcast(self) -> None:
        """Publish a SEPARATELY-redacted view to each seat's own channel + a spectator
        channel. Each payload is built by the engine whitelist for that exact viewer, so
        no seat's hole cards ever appear on another seat's channel."""
        state = self.state
        for seat in state.seats:
            view = self._project(state, seat.seat_id)
            await self._publish(seat_channel(self._table_id, seat.seat_id), "state", view)
        await self._publish(
            public_channel(self._table_id), "state", self._project(state, None)
        )

    async def _send_error(self, seat: int, message: str) -> None:
        """An error visible ONLY to the offending seat (its own channel)."""
        await self._publisher.publish(
            seat_channel(self._table_id, seat),
            json.dumps({"type": "error", "seat": seat, "message": message}),
        )

    async def _publish(self, channel: str, event_type: str, view: dict[str, Any]) -> None:
        await self._publisher.publish(channel, json.dumps({"type": event_type, **view}))


# --------------------------------------------------------------------------- #
# WS gateway — one client subscribes to its OWN seat channel and is fanned to.  #
# --------------------------------------------------------------------------- #
def _get_broker() -> Broker:
    return RedisPubSub.from_url(_REDIS_URL)


router = APIRouter()


@router.websocket("/games/poker.nlhe/tables/{table_id}/seats/{seat_id}")
async def poker_seat_ws(websocket: WebSocket, table_id: str, seat_id: int) -> None:
    """Stream a single seat's REDACTED view. The connection is bound to the seat in its
    path (the same stubbed auth posture as Crash's fan-out) and subscribes ONLY to that
    seat's channel, so it can never receive another seat's hole cards."""
    await websocket.accept()
    broker = _get_broker()
    try:
        await _pump(broker, seat_channel(table_id, seat_id), websocket.send_text)
    except WebSocketDisconnect:
        pass
    finally:
        with suppress(Exception):
            await broker.aclose()


__all__ = [
    "POKER_GAME_ID",
    "InMemoryPubSub",
    "InMemoryStateStore",
    "PokerActor",
    "PokerStateStore",
    "PostgresStateStore",
    "RedisPubSub",
    "RedisStateStore",
    "public_channel",
    "router",
    "seat_channel",
]
