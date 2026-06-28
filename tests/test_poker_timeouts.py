"""S34 — turn timers, disconnect, reconnect for the realtime poker actor.

Extends the S32 ``PokerActor`` with three transport-level concerns, all proven DB-free
(in-memory pub/sub + state-store seams + a FAKE, manually-advanced clock — no Redis, no
Postgres, no wall-clock waits):

* **Per-turn timers → auto-act.** When the seat to act exceeds its turn timeout, the actor
  auto-acts THROUGH the pure reducer (``apply_action``): auto-CHECK when it faces no bet,
  else auto-FOLD. The table advances exactly as a manual action would; it never stalls on an
  absent player.
* **Disconnect → sit-out / auto-muck.** A seat marked disconnected is auto-acted the instant
  it is to act (no waiting on its own timer), so the remaining players finish the hand.
* **Reconnect → resync (SECURITY-CRITICAL).** A returning client receives a snapshot through
  the SAME engine whitelist (``public_view``) — its own hole cards plus public state, and
  NEVER another seat's holes nor the undealt board.

The clock is injected (``now_fn``) so timeouts are instant + deterministic: a timeout fires
when ``check_timeout`` is called and ``now() - turn_started_at >= turn_timeout`` — the test
advances the fake clock manually. No ``time.sleep`` anywhere.
"""

from __future__ import annotations

import json
from typing import Any

from app.ws.poker import (
    InMemoryPubSub,
    InMemoryStateStore,
    PokerActor,
    seat_channel,
)
from engine.poker.table import Street, TableConfig, TableState

_TABLE = "t-1"
_CONFIG = TableConfig(small_blind=50, big_blind=100)
_START = 10_000
_STACKS = (_START, _START, _START)
_TOTAL = _START * 3
_TIMEOUT = 30.0


def _fixed_seed() -> bytes:
    return b"poker-s34-fixed-deal-seed-32byte"  # 32 bytes — deterministic deal


class FakeClock:
    """A manually-advanced monotonic clock; ``now_fn`` calls it as ``clock()``."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _new_actor(
    broker: InMemoryPubSub,
    store: InMemoryStateStore,
    clock: FakeClock,
    **kw: Any,
) -> PokerActor:
    return PokerActor(
        _TABLE,
        _CONFIG,
        broker,
        store,
        seed_factory=_fixed_seed,
        now_fn=clock,
        turn_timeout=_TIMEOUT,
        **kw,
    )


async def _drive_preflop_to_flop(actor: PokerActor) -> None:
    """Every seat calls/checks preflop until the flop opens (current_bet resets to 0)."""
    while actor.state.street == Street.PREFLOP and not actor.state.hand_over:
        to_act = actor.state.to_act
        assert to_act is not None
        seat = actor.state.seats[to_act]
        kind = "call" if seat.street_contrib < actor.state.current_bet else "check"
        await actor.act(seat=to_act, kind=kind)


# --------------------------------------------------------------------------- #
# Per-turn timer → auto-act.                                                    #
# --------------------------------------------------------------------------- #
async def test_timeout_auto_checks_when_facing_no_bet() -> None:
    """On a street with no bet to call, a timed-out seat is auto-CHECKED (not folded): it
    stays live, is marked acted, and the turn advances to the next seat."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    await _drive_preflop_to_flop(actor)
    assert actor.state.street == Street.FLOP
    assert actor.state.current_bet == 0  # nobody has bet the flop yet
    victim = actor.state.to_act
    assert victim is not None

    clock.advance(_TIMEOUT)  # the turn clock expires
    result = await actor.check_timeout()

    assert result is not None
    assert actor.state.seats[victim].acted is True
    assert actor.state.seats[victim].folded is False  # auto-CHECK keeps the hand live
    assert actor.state.to_act != victim  # the turn moved on


async def test_timeout_auto_folds_when_facing_a_bet() -> None:
    """Facing a bet to call, a timed-out seat is auto-FOLDED; it is out of the hand and the
    turn advances — the table does not stall."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    victim = actor.state.to_act
    assert victim is not None
    seat = actor.state.seats[victim]
    assert seat.street_contrib < actor.state.current_bet  # genuinely facing the big blind

    clock.advance(_TIMEOUT)
    result = await actor.check_timeout()

    assert result is not None
    assert actor.state.seats[victim].folded is True
    assert actor.state.to_act != victim


async def test_no_timeout_before_the_deadline() -> None:
    """Before the deadline, ``check_timeout`` is a no-op: the table is untouched."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    before = actor.state

    clock.advance(_TIMEOUT - 1.0)  # not yet expired
    result = await actor.check_timeout()

    assert result is None
    assert actor.state == before  # nothing happened


async def test_table_never_stalls_under_repeated_timeouts() -> None:
    """A hand where EVERY seat repeatedly times out still terminates and settles: each
    check_timeout auto-acts one seat (fold facing a bet, else check), so the hand reaches a
    conclusion in a bounded number of steps — never a deadlock or an infinite loop."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)

    steps = 0
    bound = 4 * len(_STACKS) + 10  # generous ceiling: 4 streets * seats + slack
    while not actor.state.hand_over:
        steps += 1
        assert steps <= bound, "table stalled — auto-act did not progress the hand"
        clock.advance(_TIMEOUT)
        acted = await actor.check_timeout()
        assert acted is not None  # the deadline was reached, so an auto-act must occur

    assert actor.state.hand_over
    assert actor.state.street == Street.COMPLETE
    assert sum(s.stack for s in actor.state.seats) == _TOTAL  # chips conserved to the unit
    assert sum(actor.state.awards.values()) > 0  # the pot was pushed to a winner


# --------------------------------------------------------------------------- #
# Disconnect → sit-out / auto-muck; the table keeps moving.                     #
# --------------------------------------------------------------------------- #
async def test_disconnect_does_not_stall_the_table() -> None:
    """A disconnected seat is auto-acted the instant it is to act (no waiting on a timer),
    so the remaining players finish the hand. Here the seat to act disconnects and is
    auto-folded immediately; the other two play on to a real showdown."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    gone = actor.state.to_act
    assert gone is not None

    await actor.disconnect(gone)  # marked sit-out; auto-acted immediately (faces the bb)

    assert actor.state.seats[gone].folded is True
    assert actor.state.to_act != gone  # the table advanced past the absent player
    assert not actor.state.hand_over  # two seats remain — the hand is still live

    # The two connected seats play to completion with no further stall.
    while not actor.state.hand_over:
        to_act = actor.state.to_act
        assert to_act is not None
        seat = actor.state.seats[to_act]
        kind = "call" if seat.street_contrib < actor.state.current_bet else "check"
        await actor.act(seat=to_act, kind=kind)

    assert actor.state.hand_over
    contested = [s.seat_id for s in actor.state.seats if not s.folded]
    assert len(contested) == 2  # a genuine showdown between the two who stayed
    assert sum(s.stack for s in actor.state.seats) == _TOTAL


async def test_disconnected_seat_is_auto_acted_when_its_turn_arrives() -> None:
    """A seat that disconnects while it is NOT its turn is auto-acted the moment the action
    reaches it — it never blocks the seat after it."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    first = actor.state.to_act
    assert first is not None
    # Disconnect a DIFFERENT seat that has not yet acted this round.
    later = next(s.seat_id for s in actor.state.seats if s.seat_id != first)
    await actor.disconnect(later)
    # `later` is not to act yet, so it has not been auto-acted by the mere disconnect
    # unless it happened to already be next; drive the live first seat and confirm the
    # disconnected seat gets auto-folded/checked rather than stalling the hand.
    while not actor.state.hand_over and actor.state.to_act not in (None,):
        to_act = actor.state.to_act
        assert to_act is not None
        if to_act == later:
            # If we ever land on the disconnected seat as to_act, the drain failed.
            raise AssertionError("disconnected seat was left to_act — table stalled")
        seat = actor.state.seats[to_act]
        kind = "call" if seat.street_contrib < actor.state.current_bet else "check"
        await actor.act(seat=to_act, kind=kind)

    assert actor.state.hand_over
    assert sum(s.stack for s in actor.state.seats) == _TOTAL


# --------------------------------------------------------------------------- #
# ★ Reconnect → resync restores the correct REDACTED view (no leak).           #
# --------------------------------------------------------------------------- #
def _snapshot_to(log: list[tuple[str, str]], seat: int) -> dict[str, Any]:
    """The LAST resync state payload delivered to ``seat``'s channel (parsed)."""
    target = seat_channel(_TABLE, seat)
    snapshots = [
        json.loads(m)
        for c, m in log
        if c == target and json.loads(m).get("type") == "state"
    ]
    assert snapshots, f"no resync snapshot was delivered to seat {seat}"
    return snapshots[-1]


async def test_reconnect_restores_own_hole_and_leaks_no_other_seat() -> None:
    """The headline security property on the new code path: after reconnect, the snapshot
    delivered to seat X's channel contains X's OWN hole cards and NO other seat's holes
    (and never the undealt board). Asserted against the actual serialized payload."""
    broker = InMemoryPubSub()
    clock = FakeClock()
    actor = _new_actor(broker, InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    # Seat 2 is the big blind (not to act preflop) — disconnecting it leaves its hand intact.
    assert actor.state.to_act != 2
    await actor.disconnect(2)
    assert not actor.state.seats[2].folded  # not its turn, so not auto-acted yet

    mark = len(broker.log)
    await actor.reconnect(2)

    new_msgs = broker.log[mark:]
    # Every resync frame goes ONLY to seat 2's channel.
    for channel, _msg in new_msgs:
        assert channel == seat_channel(_TABLE, 2), (
            f"resync wrote to {channel}, not seat 2's channel"
        )

    snapshot = _snapshot_to(new_msgs, 2)
    raw = json.dumps(snapshot)
    # No undealt board, under any spelling.
    assert "communityUndealt" not in snapshot
    assert "community_undealt" not in raw
    own_seen = False
    for s in snapshot["seats"]:
        if s["seatId"] == 2:
            assert "hole" in s and len(s["hole"]) == 2  # seat 2 sees its OWN holes
            own_seen = True
        else:
            assert "hole" not in s, f"LEAK: seat {s['seatId']}'s hole in seat 2's snapshot"
    assert own_seen, "seat 2's own holes were missing from its resync — resync is broken"


async def test_reconnect_snapshot_detector_fires_on_a_leaky_projection() -> None:
    """RED-quality guard: prove the reconnect no-leak assertion is load-bearing by wiring a
    NAIVE projection that dumps every hole, and showing the same parse-and-assert catches
    it. (If the assertion silently skipped snapshots, this would pass and mislead.)"""

    def _leaky_view(state: TableState, viewer: int | None) -> dict[str, Any]:  # noqa: ARG001
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
        }

    broker = InMemoryPubSub()
    clock = FakeClock()
    actor = _new_actor(
        broker, InMemoryStateStore(), clock, view_projection=_leaky_view
    )
    await actor.start_hand(_STACKS, button=0)
    mark = len(broker.log)
    await actor.reconnect(2)

    snapshot = _snapshot_to(broker.log[mark:], 2)
    leaked = [
        s["seatId"]
        for s in snapshot["seats"]
        if s["seatId"] != 2 and "hole" in s
    ]
    assert leaked, "detector did not observe the leak — the no-leak test would be vacuous"


async def test_reconnect_clears_sit_out_so_the_seat_resumes_duties() -> None:
    """Reconnect clears sit-out (the documented rule): a seat that returns BEFORE its turn
    is no longer auto-acted — when the action reaches it, it is left to_act awaiting its own
    input, exactly as if it had never dropped."""
    clock = FakeClock()
    actor = _new_actor(InMemoryPubSub(), InMemoryStateStore(), clock)
    await actor.start_hand(_STACKS, button=0)
    # Seat 2 (big blind) is not to act preflop; disconnect then reconnect it before its turn.
    assert actor.state.to_act != 2
    await actor.disconnect(2)
    assert not actor.state.seats[2].folded  # not its turn → not auto-acted yet
    await actor.reconnect(2)  # sit-out cleared

    # Drive the other seats; the action must REACH seat 2 (it is not auto-folded past).
    reached_seat_2 = False
    while not actor.state.hand_over:
        to_act = actor.state.to_act
        assert to_act is not None
        if to_act == 2:
            reached_seat_2 = True
            assert not actor.state.seats[2].folded  # awaiting its own input, still live
            break
        seat = actor.state.seats[to_act]
        kind = "call" if seat.street_contrib < actor.state.current_bet else "check"
        await actor.act(seat=to_act, kind=kind)

    assert reached_seat_2, "reconnected seat 2 was auto-acted past — sit-out was not cleared"
