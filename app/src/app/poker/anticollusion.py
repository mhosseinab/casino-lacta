"""Anti-collusion signals — OBSERVE-AND-EMIT detectors for poker collusion patterns.

S35, the last P6 step. Three detectors read already-recorded hand / session data and emit
structured risk EVENTS for HUMAN review:

* **chip-dumping** — one player consistently transfers chips ONE-WAY to a confederate
  (loses to them every hand, never wins back): a strongly directional net flow.
* **soft-play** — two players refuse to contest EACH OTHER (fold to one another far more
  than they fold to the field) while playing the field normally: pair-specific avoidance,
  measured RELATIVE to each player's behaviour vs the field so tight-but-honest play is not
  flagged.
* **shared-IP/device** — two or more distinct accounts at one table share an IP or device
  fingerprint (one human, many seats).

★ THE POSTURE (CLAUDE.md realtime-integrity). These are ADVISORY signals, never enforcement.
A detector NEVER auto-bans, folds a hand, mutates table state, alters a payout, or blocks a
bet — it only emits a structured :class:`RiskEvent`. The server stays authoritative; humans
act on the signals. Money is integer minor units (net flows are signed minor units; the only
floats here are detection *rates*, never a stored or settled amount).

The detectors are PURE functions over their fixture inputs — deterministic and unit-testable,
no clock, no IO, no randomness — so the same recorded data always raises the same signals.
Thresholds are CONFIG (:class:`DetectorConfig`), injected, never literals buried in logic.

Emission rides the small :class:`RiskLog` seam (ISP — one ``emit``). The durable adapter
(:class:`AuditRiskLog`) writes to the EXISTING ``audit_events`` table (reuse, not a parallel
log); :class:`InMemoryRiskLog` is the test double. Wiring the detectors into the live table
actor's hand-end hook is a later observability concern (S39) — this step owns the detectors,
the event shape, and the emission seam.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations
from typing import Protocol, runtime_checkable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent

# --------------------------------------------------------------------------- recorded inputs


@dataclass(frozen=True)
class HandRecord:
    """A distilled summary of ONE completed hand — the datum a table actor knows at hand end.

    Seat-keyed (seats rotate; ``players`` maps each seat to its occupant for the hand):

    * ``net_minor`` — signed net chip change per seat this hand (winner positive, loser
      negative), in minor units. The chip-dumping flow basis.
    * ``voluntary`` — seats that voluntarily put chips in the pot (VPIP) this hand.
    * ``folds_to`` — folder seat -> the seat whose aggression it folded to (the heads-up
      confrontation outcome). The soft-play fold basis.
    * ``showdown`` — seats that reached showdown (contested to the end). The soft-play
      contest basis.
    """

    table_id: str
    hand_no: int
    players: Mapping[int, str]
    net_minor: Mapping[int, int]
    voluntary: frozenset[int] = frozenset()
    folds_to: Mapping[int, int] = field(default_factory=dict)
    showdown: frozenset[int] = frozenset()


@dataclass(frozen=True)
class SeatSession:
    """A seat's connection fingerprint at a table — the shared-IP/device detector input."""

    table_id: str
    seat: int
    user_id: str
    ip: str
    device_id: str


# --------------------------------------------------------------------------- risk events


class RiskSignal(StrEnum):
    """The three collusion signal kinds (advisory; never an enforcement action)."""

    CHIP_DUMPING = "chip_dumping"
    SOFT_PLAY = "soft_play"
    SHARED_DEVICE = "shared_device"


class Severity(StrEnum):
    """Triage hint for the human reviewer — NOT an auto-action threshold."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RiskEvent:
    """A structured, advisory risk record for human review. Carries the signal, the table,
    the implicated users/seats, a triage ``severity``, and an ``evidence`` summary (the
    numbers that fired the signal). It records NOTHING that could enforce a ban — it is a
    note, not an action."""

    signal: RiskSignal
    table_id: str
    user_ids: tuple[str, ...]
    seats: tuple[int, ...]
    severity: Severity
    evidence: Mapping[str, object]

    @property
    def event_type(self) -> str:
        """The ``audit_events.type`` discriminator for this signal."""
        return f"risk.collusion.{self.signal.value}"

    def to_payload(self) -> dict[str, object]:
        """JSON-safe payload for the audit log (camelCase, matching the API envelope)."""
        return {
            "signal": self.signal.value,
            "tableId": self.table_id,
            "userIds": list(self.user_ids),
            "seats": list(self.seats),
            "severity": self.severity.value,
            "evidence": dict(self.evidence),
        }


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class DetectorConfig:
    """Detection thresholds — CONFIG, injected, never literals in the logic. The ratio/rate
    fields are detection statistics (dimensionless), NOT money."""

    # chip-dumping: a pair flags when one direction dominates over a sustained run.
    chip_dump_min_hands: int = 5  # min one-way dump hands before a pair is considered
    chip_dump_min_total_minor: int = 1  # ignore negligible flows (minor units)
    chip_dump_ratio: float = 4.0  # dominant-direction total >= ratio * reverse total

    # soft-play: a pair flags when it folds to ITSELF far more than to the field.
    soft_min_confrontations: int = 5  # min pair confrontations before judging
    soft_abs_fold_rate: float = 0.8  # pair must fold-to-each-other at least this often
    soft_relative_factor: float = 2.0  # ... and this many x its own fold-to-field rate


DEFAULT_CONFIG = DetectorConfig()


# --------------------------------------------------------------------------- chip-dumping


def detect_chip_dumping(
    hands: Sequence[HandRecord], *, config: DetectorConfig = DEFAULT_CONFIG
) -> list[RiskEvent]:
    """Flag pairs whose net chip flow is strongly ONE-WAY across many hands.

    Per hand, each net loser's loss is attributed to the hand's single largest net winner
    (the beneficiary). Accumulated per ORDERED user pair, a dump from ``a`` to ``b`` fires
    when, within a table: the count of a->b dump hands >= ``chip_dump_min_hands`` AND the
    a->b total is >= ``chip_dump_ratio`` x the reverse (b->a) total (so honest two-way trading
    never trips). Balanced play yields symmetric flow → no event.
    """
    events: list[RiskEvent] = []
    for table_id, table_hands in _group_by_table(hands).items():
        # directed[(a, b)] = (total minor a dumped to b, count of a->b dump hands)
        directed: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        seats_by_user: dict[str, set[int]] = defaultdict(set)
        for hand in table_hands:
            for seat, user in hand.players.items():
                seats_by_user[user].add(seat)
            winner = _top_winner(hand)
            if winner is None:
                continue
            win_user = hand.players[winner]
            for seat, net in hand.net_minor.items():
                if net >= 0:
                    continue
                loser = hand.players.get(seat)
                if loser is None or loser == win_user:
                    continue
                bucket = directed[(loser, win_user)]
                bucket[0] += -net
                bucket[1] += 1

        seen: set[frozenset[str]] = set()
        for (a, b), (total_ab, count_ab) in sorted(directed.items()):
            pair = frozenset({a, b})
            if pair in seen:
                continue
            total_ba, _ = directed.get((b, a), [0, 0])
            if (
                count_ab >= config.chip_dump_min_hands
                and total_ab >= config.chip_dump_min_total_minor
                and total_ab >= config.chip_dump_ratio * total_ba
            ):
                seen.add(pair)
                events.append(
                    _event(
                        RiskSignal.CHIP_DUMPING,
                        table_id,
                        dumper=a,
                        beneficiary=b,
                        seats=seats_by_user[a] | seats_by_user[b],
                        severity=_dump_severity(count_ab),
                        evidence={
                            "dumper": a,
                            "beneficiary": b,
                            "dumpHands": count_ab,
                            "totalDumpedMinor": total_ab,
                            "reverseTotalMinor": total_ba,
                        },
                    )
                )
    return events


def _top_winner(hand: HandRecord) -> int | None:
    """The seat with the largest positive net this hand (the dump beneficiary), or ``None``
    if nobody netted positive. Ties resolve to the lowest seat id (deterministic)."""
    best_seat: int | None = None
    best_net = 0
    for seat in sorted(hand.net_minor):
        net = hand.net_minor[seat]
        if net > best_net:
            best_net = net
            best_seat = seat
    return best_seat


def _dump_severity(count: int) -> Severity:
    return Severity.HIGH if count >= 2 * DEFAULT_CONFIG.chip_dump_min_hands else Severity.MEDIUM


# --------------------------------------------------------------------------- soft-play


def detect_soft_play(
    hands: Sequence[HandRecord], *, config: DetectorConfig = DEFAULT_CONFIG
) -> list[RiskEvent]:
    """Flag pairs that avoid contesting EACH OTHER, measured relative to the field.

    For a table, a confrontation between two users is either a fold (one folds to the other)
    or a contested showdown (both reach showdown). A pair flags when, within a table:
    confrontations >= ``soft_min_confrontations``; the pair's fold-to-each-other rate >=
    ``soft_abs_fold_rate``; AND that rate >= ``soft_relative_factor`` x each player's own
    fold rate vs the FIELD. The relative test is what stops two tight-but-honest players
    (who fold a lot to everyone) from tripping — only pair-SPECIFIC avoidance flags.
    """
    events: list[RiskEvent] = []
    for table_id, table_hands in _group_by_table(hands).items():
        # fold[(p, q)] = hands where user p folded to user q; contest[{p,q}] = mutual showdowns.
        fold: dict[tuple[str, str], int] = defaultdict(int)
        contest: dict[frozenset[str], int] = defaultdict(int)
        seats_by_user: dict[str, set[int]] = defaultdict(set)
        users: set[str] = set()
        for hand in table_hands:
            for seat, user in hand.players.items():
                seats_by_user[user].add(seat)
                users.add(user)
            for folder_seat, agg_seat in hand.folds_to.items():
                folder = hand.players.get(folder_seat)
                agg = hand.players.get(agg_seat)
                if folder is not None and agg is not None and folder != agg:
                    fold[(folder, agg)] += 1
            shown = [hand.players[s] for s in hand.showdown if s in hand.players]
            for p, q in combinations(sorted(set(shown)), 2):
                contest[frozenset({p, q})] += 1

        for a, b in combinations(sorted(users), 2):
            pair = frozenset({a, b})
            pair_fold = fold[(a, b)] + fold[(b, a)]
            pair_confront = pair_fold + contest[pair]
            if pair_confront < config.soft_min_confrontations:
                continue
            pair_rate = pair_fold / pair_confront
            if pair_rate < config.soft_abs_fold_rate:
                continue
            field_rate = max(
                _field_fold_rate(a, b, fold, contest, users),
                _field_fold_rate(b, a, fold, contest, users),
            )
            if pair_rate < config.soft_relative_factor * field_rate:
                continue
            events.append(
                _event(
                    RiskSignal.SOFT_PLAY,
                    table_id,
                    dumper=a,
                    beneficiary=b,
                    seats=seats_by_user[a] | seats_by_user[b],
                    severity=Severity.MEDIUM,
                    evidence={
                        "pairConfrontations": pair_confront,
                        "pairFolds": pair_fold,
                        "pairFoldRate": round(pair_rate, 4),
                        "fieldFoldRate": round(field_rate, 4),
                    },
                )
            )
    return events


def _field_fold_rate(
    player: str,
    partner: str,
    fold: Mapping[tuple[str, str], int],
    contest: Mapping[frozenset[str], int],
    users: Iterable[str],
) -> float:
    """``player``'s fold rate in confrontations vs the FIELD (everyone except ``partner``).
    Zero when the player never confronted the field (no baseline → relative test is then
    driven purely by the absolute threshold)."""
    folds = 0
    confronts = 0
    for other in users:
        if other == player or other == partner:
            continue
        f = fold[(player, other)]
        c = contest[frozenset({player, other})]
        folds += f
        confronts += f + c
    return folds / confronts if confronts else 0.0


# --------------------------------------------------------------------------- shared device


def detect_shared_device(
    sessions: Sequence[SeatSession], *, config: DetectorConfig = DEFAULT_CONFIG
) -> list[RiskEvent]:
    """Flag tables where >=2 DISTINCT users share an IP or device fingerprint.

    A shared fingerprint (one human at multiple seats) is the strongest mechanical collusion
    tell. ONE event per (table, colluding user-set): a pair sharing BOTH an IP and a device
    is a single signal carrying both pieces of evidence, not two. Sessions from the same user
    (e.g. a reconnect) are not a signal.
    """
    events: list[RiskEvent] = []
    for table_id, table_sessions in _group_sessions_by_table(sessions).items():
        # colluding user-set -> (matched fingerprints, involved seats)
        matches: dict[frozenset[str], tuple[list[dict[str, str]], set[int]]] = defaultdict(
            lambda: ([], set())
        )
        for attr, label in (("ip", "ip"), ("device_id", "device")):
            grouped: dict[str, list[SeatSession]] = defaultdict(list)
            for s in table_sessions:
                grouped[getattr(s, attr)].append(s)
            for fingerprint, members in sorted(grouped.items()):
                distinct_users = frozenset(s.user_id for s in members)
                if len(distinct_users) < 2:
                    continue
                fingerprints, seats = matches[distinct_users]
                fingerprints.append({"kind": label, "value": fingerprint})
                seats.update(s.seat for s in members)
        for users, (fingerprints, seats) in sorted(matches.items(), key=lambda kv: sorted(kv[0])):
            events.append(
                _event(
                    RiskSignal.SHARED_DEVICE,
                    table_id,
                    users=set(users),
                    seats=seats,
                    severity=Severity.HIGH,
                    evidence={
                        "sharedBy": sorted(users),
                        "fingerprints": fingerprints,
                    },
                )
            )
    return events


# --------------------------------------------------------------------------- scan + emission


def scan_table(
    hands: Sequence[HandRecord],
    sessions: Sequence[SeatSession],
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> list[RiskEvent]:
    """Run all three detectors over a table's recorded hands + sessions and return every
    risk event (observe-only; nothing is enforced)."""
    return [
        *detect_chip_dumping(hands, config=config),
        *detect_soft_play(hands, config=config),
        *detect_shared_device(sessions, config=config),
    ]


@runtime_checkable
class RiskLog(Protocol):
    """The emission seam (ISP — one consumer-shaped method). Implementations append/persist
    a risk event; they NEVER enforce. Async so the durable adapter can hit Postgres."""

    async def emit(self, event: RiskEvent) -> None: ...


class InMemoryRiskLog:
    """Test/local sink: collects emitted events in order. No IO."""

    def __init__(self) -> None:
        self.events: list[RiskEvent] = []

    async def emit(self, event: RiskEvent) -> None:
        self.events.append(event)


class AuditRiskLog:
    """The durable adapter — writes each risk event as a row in the EXISTING ``audit_events``
    table (``type=risk.collusion.*``, ``payload=evidence``, no bet/round link). Reuses the
    audit log rather than standing up a parallel one. Append-only and advisory: it records the
    signal for human review and moves NO money / bans nobody."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def emit(self, event: RiskEvent) -> None:
        async with self._sf() as session, session.begin():
            session.add(
                AuditEvent(
                    id=uuid4().hex,
                    bet_id=None,
                    round_id=None,
                    type=event.event_type,
                    payload=event.to_payload(),
                )
            )


async def emit_risk_events(log: RiskLog, events: Iterable[RiskEvent]) -> None:
    """Emit each event to the risk log (observe-only — emission is the whole action)."""
    for event in events:
        await log.emit(event)


# --------------------------------------------------------------------------- helpers


def _group_by_table(hands: Sequence[HandRecord]) -> dict[str, list[HandRecord]]:
    grouped: dict[str, list[HandRecord]] = defaultdict(list)
    for hand in hands:
        grouped[hand.table_id].append(hand)
    return grouped


def _group_sessions_by_table(
    sessions: Sequence[SeatSession],
) -> dict[str, list[SeatSession]]:
    grouped: dict[str, list[SeatSession]] = defaultdict(list)
    for s in sessions:
        grouped[s.table_id].append(s)
    return grouped


def _event(
    signal: RiskSignal,
    table_id: str,
    *,
    severity: Severity,
    evidence: Mapping[str, object],
    seats: set[int],
    dumper: str | None = None,
    beneficiary: str | None = None,
    users: set[str] | None = None,
) -> RiskEvent:
    if users is None:
        assert dumper is not None and beneficiary is not None
        users = {dumper, beneficiary}
    return RiskEvent(
        signal=signal,
        table_id=table_id,
        user_ids=tuple(sorted(users)),
        seats=tuple(sorted(seats)),
        severity=severity,
        evidence=evidence,
    )


__all__ = [
    "DEFAULT_CONFIG",
    "AuditRiskLog",
    "DetectorConfig",
    "HandRecord",
    "InMemoryRiskLog",
    "RiskEvent",
    "RiskLog",
    "RiskSignal",
    "SeatSession",
    "Severity",
    "detect_chip_dumping",
    "detect_shared_device",
    "detect_soft_play",
    "emit_risk_events",
    "scan_table",
]
