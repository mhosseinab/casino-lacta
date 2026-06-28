"""S35 — anti-collusion signals: chip-dumping, soft-play, shared-IP/device.

These are OBSERVE-AND-EMIT detectors (CLAUDE.md realtime-integrity posture): they consume
already-recorded hand / session data and emit structured risk EVENTS for human review. They
NEVER auto-ban, fold a hand, mutate table state, or move money — they are advisory signals.

The detectors are PURE functions over recorded fixtures (deterministic, unit-testable): given
the same data they raise the same signals, so they can be tested without any infrastructure.
Emission rides a small ``RiskLog`` seam; the durable adapter writes to the existing
``audit_events`` table (no parallel log invented).

The load-bearing property under test: synthetic collusion fixtures raise the EXPECTED signal,
and a clean-play fixture raises NONE of the three (no false positives on the fixtures).
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent
from app.poker.anticollusion import (
    AuditRiskLog,
    HandRecord,
    InMemoryRiskLog,
    RiskSignal,
    SeatSession,
    Severity,
    detect_chip_dumping,
    detect_shared_device,
    detect_soft_play,
    emit_risk_events,
    scan_table,
)

TABLE = "poker-t1"
ALICE, BOB, CAROL, DAVE = "alice", "bob", "carol", "dave"


# --------------------------------------------------------------------------- fixture builders
def _hand(
    hand_no: int,
    players: Mapping[int, str],
    net_minor: Mapping[int, int],
    *,
    voluntary: frozenset[int] = frozenset(),
    folds_to: Mapping[int, int] | None = None,
    showdown: frozenset[int] = frozenset(),
    table_id: str = TABLE,
) -> HandRecord:
    return HandRecord(
        table_id=table_id,
        hand_no=hand_no,
        players=dict(players),
        net_minor=dict(net_minor),
        voluntary=voluntary,
        folds_to=dict(folds_to or {}),
        showdown=showdown,
    )


def _chip_dump_hands(table_id: str = TABLE) -> list[HandRecord]:
    """Seat 0 (Alice) consistently dumps chips ONE-WAY to seat 1 (Bob): she calls/all-ins
    into Bob and loses every time, never winning back. A clear directional transfer."""
    players = {0: ALICE, 1: BOB}
    return [
        _hand(
            i,
            players,
            {0: -1000, 1: 1000},
            voluntary=frozenset({0, 1}),
            showdown=frozenset({0, 1}),
            table_id=table_id,
        )
        for i in range(8)
    ]


def _clean_money_hands() -> list[HandRecord]:
    """Alice and Bob trade pots back and forth roughly evenly — normal winning/losing.
    Net flow is balanced in BOTH directions, so no one-way transfer exists."""
    players = {0: ALICE, 1: BOB}
    hands: list[HandRecord] = []
    for i in range(8):
        # Alternate who wins; equal magnitudes → balanced two-way flow.
        if i % 2 == 0:
            net = {0: 1000, 1: -1000}
        else:
            net = {0: -1000, 1: 1000}
        hands.append(
            _hand(i, players, net, voluntary=frozenset({0, 1}), showdown=frozenset({0, 1}))
        )
    return hands


def _soft_play_hands(table_id: str = TABLE) -> list[HandRecord]:
    """Alice (0) and Bob (1) refuse to contest each other: whenever both enter a pot, one
    folds to the other (never a showdown between them). Yet both happily contest the field
    (Carol, seat 2) to showdown — so the avoidance is SPECIFIC to the pair, not tightness."""
    hands: list[HandRecord] = []
    players_pair = {0: ALICE, 1: BOB}
    players_field = {0: ALICE, 1: BOB, 2: CAROL}
    hn = 0
    # 8 pair hands: both voluntary, one always folds to the other (alternating direction).
    for i in range(8):
        if i % 2 == 0:
            folds = {0: 1}  # Alice folds to Bob
        else:
            folds = {1: 0}  # Bob folds to Alice
        hands.append(
            _hand(
                hn,
                players_pair,
                {0: 0, 1: 0},
                voluntary=frozenset({0, 1}),
                folds_to=folds,
                table_id=table_id,
            )
        )
        hn += 1
    # 8 field hands: Alice and Bob each go to showdown vs Carol (they DO contest the field).
    for i in range(8):
        hands.append(
            _hand(
                hn,
                players_field,
                {0: 0, 1: 0, 2: 0},
                voluntary=frozenset({0, 2}) if i % 2 == 0 else frozenset({1, 2}),
                showdown=frozenset({0, 2}) if i % 2 == 0 else frozenset({1, 2}),
                table_id=table_id,
            )
        )
        hn += 1
    return hands


def _clean_play_hands() -> list[HandRecord]:
    """Alice and Bob play each other normally: when both enter, they go to showdown as often
    as anyone — low fold-to-each-other rate."""
    players = {0: ALICE, 1: BOB}
    hands: list[HandRecord] = []
    for i in range(8):
        if i % 4 == 0:
            # occasional fold (normal), but mostly showdowns
            hands.append(
                _hand(i, players, {0: -500, 1: 500}, voluntary=frozenset({0, 1}), folds_to={0: 1})
            )
        else:
            hands.append(
                _hand(
                    i,
                    players,
                    {0: 500, 1: -500} if i % 2 == 0 else {0: -500, 1: 500},
                    voluntary=frozenset({0, 1}),
                    showdown=frozenset({0, 1}),
                )
            )
    return hands


def _tight_honest_pair_hands() -> list[HandRecord]:
    """A tight-but-HONEST pair: Alice (0) and Bob (1) fold to EACH OTHER at the maximum
    absolute rate (1.0 — every pair confrontation is a fold), so the ABSOLUTE soft-play
    threshold alone WOULD flag them. But they are simply nits who fold to EVERYONE: each also
    folds to the field (Carol, seat 2) at a proportionally high rate (6/8 = 0.75). Only the
    relative-to-field guard tells them apart from a colluding pair:
    ``pair_rate (1.0) < soft_relative_factor (2.0) * field_rate (0.75) = 1.5`` → SUPPRESSED.

    This is the load-bearing no-false-positive fixture: a detector that drops the relative
    guard (``_field_fold_rate`` → 0.0) would compute ``1.0 < 0`` (False) and WRONGLY fire,
    so this test fails against that mutation and passes only with the real guard intact."""
    hands: list[HandRecord] = []
    players_pair = {0: ALICE, 1: BOB}
    hn = 0
    # 6 pair confrontations, all folds (alternating direction) → pair fold-rate = 1.0.
    for i in range(6):
        folds = {0: 1} if i % 2 == 0 else {1: 0}
        hands.append(
            _hand(hn, players_pair, {0: 0, 1: 0}, voluntary=frozenset({0, 1}), folds_to=folds)
        )
        hn += 1
    # Each of Alice/Bob confronts the field (Carol) 8x: folds 6, shows down 2 → field-rate 0.75.
    for player_seat in (0, 1):
        user = ALICE if player_seat == 0 else BOB
        players_field = {player_seat: user, 2: CAROL}
        for i in range(8):
            if i < 6:
                hands.append(
                    _hand(
                        hn,
                        players_field,
                        {player_seat: 0, 2: 0},
                        voluntary=frozenset({player_seat, 2}),
                        folds_to={player_seat: 2},
                    )
                )
            else:
                hands.append(
                    _hand(
                        hn,
                        players_field,
                        {player_seat: 0, 2: 0},
                        voluntary=frozenset({player_seat, 2}),
                        showdown=frozenset({player_seat, 2}),
                    )
                )
            hn += 1
    return hands


def _shared_device_sessions() -> list[SeatSession]:
    """Two seats at one table share an IP + device fingerprint (two accounts, one machine)."""
    return [
        SeatSession(table_id=TABLE, seat=0, user_id=ALICE, ip="10.0.0.5", device_id="devA"),
        SeatSession(table_id=TABLE, seat=1, user_id=BOB, ip="10.0.0.5", device_id="devA"),
        SeatSession(table_id=TABLE, seat=2, user_id=CAROL, ip="10.0.0.9", device_id="devC"),
    ]


def _distinct_device_sessions() -> list[SeatSession]:
    """Every seat has a distinct IP and device — no shared fingerprint."""
    return [
        SeatSession(table_id=TABLE, seat=0, user_id=ALICE, ip="10.0.0.5", device_id="devA"),
        SeatSession(table_id=TABLE, seat=1, user_id=BOB, ip="10.0.0.6", device_id="devB"),
        SeatSession(table_id=TABLE, seat=2, user_id=CAROL, ip="10.0.0.9", device_id="devC"),
    ]


# --------------------------------------------------------------------------- chip-dumping
def test_chip_dumping_flags_one_way_transfers() -> None:
    events = detect_chip_dumping(_chip_dump_hands())
    assert len(events) == 1
    ev = events[0]
    assert ev.signal is RiskSignal.CHIP_DUMPING
    assert set(ev.user_ids) == {ALICE, BOB}
    assert ev.table_id == TABLE
    assert ev.severity in (Severity.MEDIUM, Severity.HIGH)


def test_chip_dumping_ignores_balanced_two_way_flow() -> None:
    assert detect_chip_dumping(_clean_money_hands()) == []


# --------------------------------------------------------------------------- soft-play
def test_soft_play_flags_pair_specific_avoidance() -> None:
    events = detect_soft_play(_soft_play_hands())
    assert len(events) == 1
    ev = events[0]
    assert ev.signal is RiskSignal.SOFT_PLAY
    assert set(ev.user_ids) == {ALICE, BOB}


def test_soft_play_ignores_normal_contesting_play() -> None:
    assert detect_soft_play(_clean_play_hands()) == []


def test_soft_play_relative_guard_spares_tight_honest_pair() -> None:
    """A tight pair that folds to each other at the MAX absolute rate but also folds to the
    field proportionally is NOT flagged — the relative-to-field guard carries the decision.
    This fails against a gutted ``_field_fold_rate`` (→0.0), proving the guard is live."""
    assert detect_soft_play(_tight_honest_pair_hands()) == []


# --------------------------------------------------------------------------- shared device
def test_shared_device_flags_shared_fingerprint() -> None:
    events = detect_shared_device(_shared_device_sessions())
    assert len(events) == 1
    ev = events[0]
    assert ev.signal is RiskSignal.SHARED_DEVICE
    assert set(ev.user_ids) == {ALICE, BOB}
    assert ev.table_id == TABLE


def test_shared_device_ignores_distinct_fingerprints() -> None:
    assert detect_shared_device(_distinct_device_sessions()) == []


# --------------------------------------------------------------------------- no false positives
def test_clean_play_raises_no_signals() -> None:
    events = scan_table(_clean_play_hands(), _distinct_device_sessions())
    assert events == []


def test_scan_table_collects_all_three_signals() -> None:
    # Distinct tables so the signals don't interfere (a real scan is per-table): the
    # chip-dump pair contests showdowns, which would otherwise dilute a same-table soft-play
    # rate. ``scan_table`` groups by table internally, so all three coexist.
    hands = _chip_dump_hands("t-dump") + _soft_play_hands("t-soft")
    events = scan_table(hands, _shared_device_sessions())
    signals = {e.signal for e in events}
    assert RiskSignal.CHIP_DUMPING in signals
    assert RiskSignal.SOFT_PLAY in signals
    assert RiskSignal.SHARED_DEVICE in signals


# --------------------------------------------------------------------------- emission
async def test_emit_writes_to_in_memory_log() -> None:
    log = InMemoryRiskLog()
    events = detect_shared_device(_shared_device_sessions())
    await emit_risk_events(log, events)
    assert log.events == events
    assert len(log.events) == 1


async def test_audit_risk_log_writes_audit_event_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The durable adapter writes a real ``audit_events`` row (REUSING the existing audit
    log, no parallel table). No money moves, no ban — just an advisory record."""
    table_id = f"poker-{uuid4().hex}"
    sessions = [
        SeatSession(table_id=table_id, seat=0, user_id=ALICE, ip="9.9.9.9", device_id="dX"),
        SeatSession(table_id=table_id, seat=1, user_id=BOB, ip="9.9.9.9", device_id="dX"),
    ]
    events = detect_shared_device(sessions)
    assert len(events) == 1

    log = AuditRiskLog(session_factory)
    await emit_risk_events(log, events)

    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.type == events[0].event_type)
            )
        ).all()
    matching = [r for r in rows if r.payload and r.payload.get("tableId") == table_id]
    assert len(matching) == 1
    payload = matching[0].payload
    assert payload is not None
    assert set(payload["userIds"]) == {ALICE, BOB}
    assert matching[0].bet_id is None
    assert matching[0].round_id is None
