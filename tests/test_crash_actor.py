"""S18 — Crash async shell: the actor's published event sequence + WS forwarding.

These exercise the thin async shell (``app.ws.crash``) over an in-memory pub/sub
(no Redis, no real sleeps), so they run in CI without infra. The binding gate:
stepping the actor through ONE round publishes ``round → tick(s) → crash``, the
commit/reveal contract holds (WAITING carries the hash but NO seed and NO C; the
crash event reveals the seed and it hashes back to the committed hash), and the
cosmetic tick multiplier never reaches/feeds C.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress

from app.ws.crash import (
    CrashActor,
    InMemoryPubSub,
    RoundTimings,
    _pump,
    cosmetic_multiplier,
)
from engine.fairness import commit

# A fixed seed whose round-1 crash point is 2.49× — enough cosmetic ticks to climb,
# not an instant bust (which would legitimately emit zero ticks).
_FIXTURE_SEED = b"actor-fixture-seed"
_INSTANT_TIMINGS = RoundTimings(waiting=0.0, tick_interval=0.0, settle=0.0)


async def _noop_sleep(_seconds: float) -> None:
    """A no-op clock so the timed loop runs instantly under test."""


def _build_actor(broker: InMemoryPubSub) -> CrashActor:
    return CrashActor(
        broker,
        seed_factory=lambda: _FIXTURE_SEED,
        id_factory=lambda n: f"round-{n}",
        timings=_INSTANT_TIMINGS,
        sleep=_noop_sleep,
    )


async def test_actor_runs_one_round_and_publishes_round_tick_crash() -> None:
    broker = InMemoryPubSub()
    rnd = await _build_actor(broker).run_round(1)

    events = [json.loads(message) for _channel, message in broker.log]
    types = [e["type"] for e in events]

    assert types[0] == "round"
    assert types[-1] == "crash"
    assert "tick" in types  # C = 2.49 → the cosmetic curve climbs before the crash
    assert rnd.C == 2.49


async def test_waiting_event_commits_without_revealing_seed_or_C() -> None:
    broker = InMemoryPubSub()
    await _build_actor(broker).run_round(1)
    round_event = json.loads(broker.log[0][1])

    assert round_event["type"] == "round"
    assert round_event["status"] == "WAITING"
    assert round_event["serverSeedHash"] == commit(_FIXTURE_SEED)
    # Commit-before-reveal: the live round must leak NEITHER the seed NOR the outcome.
    assert "serverSeed" not in round_event
    assert "crashPoint" not in round_event
    assert "C" not in round_event


async def test_crash_event_reveals_seed_that_hashes_to_the_commitment() -> None:
    broker = InMemoryPubSub()
    rnd = await _build_actor(broker).run_round(1)
    round_event = json.loads(broker.log[0][1])
    crash_event = json.loads(broker.log[-1][1])

    assert crash_event["type"] == "crash"
    assert crash_event["crashPoint"] == rnd.C
    revealed = bytes.fromhex(crash_event["serverSeed"])
    assert revealed == _FIXTURE_SEED
    assert hashlib.sha256(revealed).hexdigest() == round_event["serverSeedHash"]


async def test_cosmetic_ticks_never_reach_or_feed_C() -> None:
    broker = InMemoryPubSub()
    rnd = await _build_actor(broker).run_round(1)
    events = [json.loads(message) for _channel, message in broker.log]

    tick_multipliers = [e["multiplier"] for e in events if e["type"] == "tick"]
    assert tick_multipliers  # this fixture produces ticks
    # The displayed curve only ever climbs TOWARD C and stops below it — it is a
    # consequence of C, never an input to it.
    assert all(m < rnd.C for m in tick_multipliers)
    ticks = [e for e in events if e["type"] == "tick"]
    for e in ticks:
        assert "serverSeed" not in e
        assert "crashPoint" not in e


def test_cosmetic_multiplier_climbs_from_1_00() -> None:
    assert cosmetic_multiplier(0, 1.06) == 1.00
    assert cosmetic_multiplier(1, 1.06) == 1.06
    assert cosmetic_multiplier(5, 1.06) > cosmetic_multiplier(4, 1.06)


async def test_in_memory_pubsub_fans_out_to_every_subscriber() -> None:
    """The seam routes through pub/sub so multiple app instances each fan out to
    their own WS clients — every subscriber must receive every message."""
    broker = InMemoryPubSub()
    async with broker.subscribe("ch") as a, broker.subscribe("ch") as b:
        await broker.publish("ch", "hello")
        assert await asyncio.wait_for(anext(a), 1.0) == "hello"
        assert await asyncio.wait_for(anext(b), 1.0) == "hello"


async def test_pump_forwards_subscribed_messages_to_the_ws_sink() -> None:
    """The WS gateway's forward loop: a (decoupled) subscriber receives what the
    actor publishes — 'a WS client receives events locally', tested, not manual."""
    broker = InMemoryPubSub()
    sink: list[str] = []

    async def send(message: str) -> None:
        sink.append(message)

    task = asyncio.create_task(_pump(broker, "ch", send))
    await asyncio.sleep(0)  # let _pump enter subscribe() before we publish
    await broker.publish("ch", "a")
    await broker.publish("ch", "b")
    await asyncio.sleep(0.01)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert sink == ["a", "b"]
