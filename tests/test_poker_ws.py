"""S32 — realtime poker gateway: multi-client hand, PER-SEAT redaction, recovery.

The security headline: a poker WS actor drives the pure ``engine.poker.table`` reducer and
publishes a SEPARATELY-redacted view to each seat's own channel — so the server NEVER sends
a seat a card it shouldn't see (no hole-card leakage). These tests run entirely on the
in-memory pub/sub + state-store seams (no Redis, no Postgres), so the leakage property is
proven DB-free.

The no-leakage invariant has THREE parts, all asserted structurally (parse the serialized
payload; never substring-match card ints):
  1. a FOLDED seat's hole appears on NO other channel, EVER — including at showdown;
  2. a non-folded seat's hole appears on another channel only once ``handOver``;
  3. the hidden ``community_undealt`` board-to-come appears on NO channel, EVER.

A deliberately-leaky "broadcast the whole table" projection is exercised to prove the
detector actually FIRES (the RED), then the engine whitelist is shown to pass (the GREEN).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

import pytest
from starlette.websockets import WebSocket

import app.ws.poker as poker_mod
from app.ws.poker import (
    InMemoryPubSub,
    InMemoryStateStore,
    PokerActor,
    PostgresStateStore,
    poker_seat_ws,
    public_channel,
    seat_channel,
)
from engine.poker.table import TableConfig, TableState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_TABLE = "t-1"
_CONFIG = TableConfig(small_blind=50, big_blind=100)
_START = 10_000
_STACKS = (_START, _START, _START)
_TOTAL = _START * 3


def _fixed_seed() -> bytes:
    return b"poker-s32-fixed-deal-seed-32byte"  # 32 bytes — deterministic deal


def _new_actor(broker: InMemoryPubSub, store: InMemoryStateStore, **kw: Any) -> PokerActor:
    return PokerActor(_TABLE, _CONFIG, broker, store, seed_factory=_fixed_seed, **kw)


# --------------------------------------------------------------------------- #
# Drivers — pick legal actions from the authoritative state to reach showdown.  #
# --------------------------------------------------------------------------- #
async def _drive_to_showdown(actor: PokerActor, *, fold_seat: int | None) -> None:
    """Play the hand to completion: ``fold_seat`` folds its first turn; every other seat
    calls when it owes the bet, else checks. Reaches a river showdown (or an uncontested
    win if too many fold)."""
    while not actor.state.hand_over:
        to_act = actor.state.to_act
        assert to_act is not None
        seat = actor.state.seats[to_act]
        if to_act == fold_seat and not seat.folded:
            await actor.act(seat=to_act, kind="fold")
        elif seat.street_contrib < actor.state.current_bet:
            await actor.act(seat=to_act, kind="call")
        else:
            await actor.act(seat=to_act, kind="check")


# --------------------------------------------------------------------------- #
# The structural leak detector (shared by the RED and the GREEN).               #
# --------------------------------------------------------------------------- #
def _recipient(channel: str) -> int | None:
    """The seat a channel delivers to (``None`` = the public/spectator channel)."""
    parts = channel.split(":")
    if len(parts) >= 2 and parts[-2] == "seat":
        return int(parts[-1])
    return None


def _assert_no_leak(log: list[tuple[str, str]]) -> bool:
    """Assert the three-part no-leakage invariant over EVERY published payload. Returns
    whether a legitimate showdown reveal was observed (so the caller can prove the test is
    not vacuously passing because holes never appear at all)."""
    saw_legit_showdown_reveal = False
    for channel, message in log:
        payload = json.loads(message)
        if payload.get("type") != "state":
            continue
        recipient = _recipient(channel)
        # (3) the hidden board-to-come is never serialized, under any spelling.
        assert "communityUndealt" not in payload, f"LEAK: undealt board on {channel}"
        assert "community_undealt" not in message, f"LEAK: undealt board on {channel}"
        for seat in payload["seats"]:
            if "hole" not in seat:
                continue
            owner = seat["seatId"]
            if owner == recipient:
                continue  # your own hole — always allowed
            # Any OTHER seat's hole on this channel must be a legitimate showdown reveal:
            assert payload["handOver"] is True, (
                f"LEAK: seat {owner}'s hole on {channel} before showdown"
            )
            # (1) a folded seat is mucked — its hole is NEVER revealed, even at showdown.
            assert seat["folded"] is False, (
                f"LEAK: folded seat {owner}'s hole revealed on {channel} at showdown"
            )
            saw_legit_showdown_reveal = True
    return saw_legit_showdown_reveal


# --------------------------------------------------------------------------- #
# ★ THE HEADLINE: a full multi-client hand leaks no hole card to any seat.      #
# --------------------------------------------------------------------------- #
async def test_no_hole_card_leakage_across_full_hand() -> None:
    """A 3-handed hand plays preflop→river→showdown over the actor; seat 0 folds. Across
    EVERY message published to EVERY channel, no seat ever receives another seat's hole
    cards (and the folded seat's hand is never revealed at showdown), nor the undealt
    board. The reveal path IS exercised (two seats reach showdown) — not a vacuous pass."""
    broker = InMemoryPubSub()
    actor = _new_actor(broker, InMemoryStateStore())
    await actor.start_hand(_STACKS, button=0)
    await _drive_to_showdown(actor, fold_seat=0)

    assert actor.state.hand_over
    assert actor.state.seats[0].folded  # seat 0 mucked
    # A real (contested) showdown happened between seats 1 and 2.
    contested = [s.seat_id for s in actor.state.seats if not s.folded]
    assert contested == [1, 2]
    # Chips are conserved to the unit across the whole hand.
    assert sum(s.stack for s in actor.state.seats) == _TOTAL

    saw_reveal = _assert_no_leak(broker.log)
    assert saw_reveal, "the showdown-reveal path was never exercised — test is vacuous"


async def test_naive_full_broadcast_leaks_holes_the_RED() -> None:
    """The RED: an actor wired with a NAIVE 'broadcast the whole table' projection (every
    hole + the undealt board to every channel) is caught by the SAME detector — proving
    the detector fires on a real leak (and the redaction in the headline test is load-
    bearing, not a no-op)."""

    def _leaky_full_view(state: TableState, viewer: int | None) -> dict[str, Any]:  # noqa: ARG001
        return {
            "handOver": state.hand_over,
            "seats": [
                {
                    "seatId": s.seat_id,
                    "folded": s.folded,
                    "hole": [{"rank": c.rank, "suit": c.suit} for c in s.hole],
                }
                for s in state.seats
            ],
            "communityUndealt": [
                {"rank": c.rank, "suit": c.suit} for c in state.community_undealt
            ],
        }

    broker = InMemoryPubSub()
    actor = _new_actor(broker, InMemoryStateStore(), view_projection=_leaky_full_view)
    await actor.start_hand(_STACKS, button=0)  # one broadcast is enough to leak

    with pytest.raises(AssertionError, match="LEAK"):
        _assert_no_leak(broker.log)


# --------------------------------------------------------------------------- #
# Multi-client delivery: capture what each subscribed seat ACTUALLY receives.   #
# --------------------------------------------------------------------------- #
async def _drain(stream: AsyncIterator[str], sink: list[str]) -> None:
    async for message in stream:
        sink.append(message)


async def test_each_subscribed_client_receives_only_its_own_redacted_view() -> None:
    """Three clients subscribe to their seat channels (a spectator to the public channel)
    BEFORE the hand; we capture every frame the transport actually delivers to each and
    re-run the leak invariant on those per-client captures."""
    broker = InMemoryPubSub()
    actor = _new_actor(broker, InMemoryStateStore())
    sinks: dict[int | None, list[str]] = {0: [], 1: [], 2: [], None: []}

    async with (
        broker.subscribe(seat_channel(_TABLE, 0)) as s0,
        broker.subscribe(seat_channel(_TABLE, 1)) as s1,
        broker.subscribe(seat_channel(_TABLE, 2)) as s2,
        broker.subscribe(public_channel(_TABLE)) as sp,
    ):
        collectors = [
            asyncio.create_task(_drain(stream, sinks[key]))
            for key, stream in ((0, s0), (1, s1), (2, s2), (None, sp))
        ]
        await asyncio.sleep(0)  # let every subscriber enter the stream before publishing
        await actor.start_hand(_STACKS, button=0)
        await _drive_to_showdown(actor, fold_seat=0)
        await asyncio.sleep(0.02)
        for collector in collectors:
            collector.cancel()
        for collector in collectors:
            with suppress(asyncio.CancelledError):
                await collector

    assert actor.state.hand_over
    for viewer, sink in sinks.items():
        assert sink, f"client {viewer} received nothing"
        for message in sink:
            payload = json.loads(message)
            assert "communityUndealt" not in payload
            for seat in payload["seats"]:
                if "hole" in seat and seat["seatId"] != viewer:
                    # only a legitimate, non-folded showdown reveal may appear:
                    assert payload["handOver"] is True
                    assert seat["folded"] is False
    # The folded seat (0)'s hole NEVER reaches seats 1, 2, or the spectator — ever.
    for viewer in (1, 2, None):
        for message in sinks[viewer]:
            for seat in json.loads(message)["seats"]:
                if seat["seatId"] == 0:
                    assert "hole" not in seat


# --------------------------------------------------------------------------- #
# Checkpoint / reload survival — the engine replay property through the store.  #
# --------------------------------------------------------------------------- #
async def _check_or_call_to_showdown(actor: PokerActor) -> None:
    """Drive to showdown with NO folds (every seat calls/checks) — for the resume test."""
    await _drive_to_showdown(actor, fold_seat=None)


async def test_actor_survives_checkpoint_and_reload_identically() -> None:
    """Drive a hand partway, then reconstruct a FRESH actor from the persisted checkpoint
    (a full restart): it resumes to the IDENTICAL state (deal-from-seed + action-log
    replay — the S31 determinism property) and continues correctly to the same result."""
    store = InMemoryStateStore()
    actor = _new_actor(InMemoryPubSub(), store)
    await actor.start_hand(_STACKS, button=0)
    # Partway: preflop completes (button calls, sb calls, bb checks → flop opens).
    while actor.state.street == actor.state.street.PREFLOP and not actor.state.hand_over:
        to_act = actor.state.to_act
        assert to_act is not None
        seat = actor.state.seats[to_act]
        if seat.street_contrib < actor.state.current_bet:
            await actor.act(seat=to_act, kind="call")
        else:
            await actor.act(seat=to_act, kind="check")
    partway = actor.state
    assert not partway.hand_over

    # A brand-new actor instance, reconstructed ONLY from the store (the restart).
    fresh = await PokerActor.reload(_TABLE, InMemoryPubSub(), store)
    assert fresh is not None
    assert fresh.state == partway  # byte-identical resume (frozen-dataclass equality)

    # Both continue from here with the SAME script → identical final states.
    await _check_or_call_to_showdown(actor)
    await _check_or_call_to_showdown(fresh)
    assert fresh.state == actor.state
    assert fresh.state.hand_over
    assert sum(s.stack for s in fresh.state.seats) == _TOTAL


# --------------------------------------------------------------------------- #
# Server-authoritative rejection — illegal / out-of-turn intent.               #
# --------------------------------------------------------------------------- #
async def test_out_of_turn_action_rejected_to_that_seat_without_mutation() -> None:
    """An intent from a seat that is not to act is rejected: the table is NOT mutated, and
    an error is published ONLY to the offending seat's channel (no state, no leak)."""
    broker = InMemoryPubSub()
    actor = _new_actor(broker, InMemoryStateStore())
    await actor.start_hand(_STACKS, button=0)
    before = actor.state
    assert before.to_act is not None
    wrong_seat = next(
        s.seat_id for s in before.seats if s.seat_id != before.to_act and not s.folded
    )
    mark = len(broker.log)

    result = await actor.act(seat=wrong_seat, kind="check")

    assert result is None
    assert actor.state == before  # the reducer rejected before any mutation
    new_messages = broker.log[mark:]
    assert len(new_messages) == 1  # exactly one message — the targeted error
    channel, message = new_messages[0]
    assert channel == seat_channel(_TABLE, wrong_seat)  # only the offender's channel
    payload = json.loads(message)
    assert payload["type"] == "error"
    assert "seats" not in payload  # an error carries no table state → nothing to leak


async def test_illegal_action_kind_rejected_to_that_seat() -> None:
    broker = InMemoryPubSub()
    actor = _new_actor(broker, InMemoryStateStore())
    await actor.start_hand(_STACKS, button=0)
    before = actor.state
    mark = len(broker.log)

    result = await actor.act(seat=before.to_act, kind="teleport")

    assert result is None
    assert actor.state == before
    new_messages = broker.log[mark:]
    assert len(new_messages) == 1
    assert json.loads(new_messages[0][1])["type"] == "error"


# --------------------------------------------------------------------------- #
# WS gateway — a seat connection is fanned ONLY its own channel.                #
# --------------------------------------------------------------------------- #
class _FakeWebSocket:
    """A minimal WebSocket double: records accept + the text frames sent."""

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


async def test_poker_seat_ws_streams_only_its_own_seat_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real ``poker_seat_ws`` handler accepts the socket, subscribes ONLY to its
    seat's channel, and forwards those frames — a frame on ANOTHER seat's channel is never
    delivered (the transport-level expression of the redaction)."""
    broker = InMemoryPubSub()
    monkeypatch.setattr(poker_mod, "_get_broker", lambda: broker)
    websocket = _FakeWebSocket()

    task = asyncio.create_task(
        poker_seat_ws(cast("WebSocket", websocket), _TABLE, 1)
    )
    await asyncio.sleep(0)  # accept() + enter subscribe() before publishing
    await broker.publish(seat_channel(_TABLE, 1), "for-seat-1")
    await broker.publish(seat_channel(_TABLE, 0), "for-seat-0")  # MUST NOT be delivered
    await asyncio.sleep(0.01)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert websocket.accepted
    assert websocket.sent == ["for-seat-1"]  # only its own channel, never seat 0's


# --------------------------------------------------------------------------- #
# Durable checkpoint to Postgres — the `poker_tables` adapter (skips w/o a DB).  #
# --------------------------------------------------------------------------- #
async def test_postgres_checkpoint_store_survives_reload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The DURABLE store: an actor checkpoints to the Postgres ``poker_tables`` row, and a
    fresh actor reloaded from that row resumes to the IDENTICAL state — the literal
    'state checkpointed to Postgres, survives a reload'. Requires a migrated Postgres
    (the conftest fixture SKIPS cleanly when none is reachable). No money column is
    touched — buy-in/rake is S33."""
    table_id = f"pg-table-{id(session_factory)}"
    store = PostgresStateStore(session_factory)
    actor = PokerActor(table_id, _CONFIG, InMemoryPubSub(), store, seed_factory=_fixed_seed)
    await actor.start_hand(_STACKS, button=0)
    # Apply a couple of preflop actions, each persisted to the DB.
    while actor.state.street == actor.state.street.PREFLOP and not actor.state.hand_over:
        to_act = actor.state.to_act
        assert to_act is not None
        seat = actor.state.seats[to_act]
        kind = "call" if seat.street_contrib < actor.state.current_bet else "check"
        await actor.act(seat=to_act, kind=kind)
    live = actor.state

    fresh = await PokerActor.reload(table_id, InMemoryPubSub(), store)
    assert fresh is not None
    assert fresh.state == live  # reconstructed from the persisted seed + action log
