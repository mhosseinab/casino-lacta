"""Crash round CORE — pure, synchronous, the single source of truth for ``C``.

No asyncio, no Redis, no WebSocket, no wall-clock. A ``CrashRound`` holds exactly
the round's identity + outcome + lifecycle state — ``{round_server_seed, round_id,
round_number, C, status}`` — and nothing else. The async shell (``app.ws.crash``)
drives the timed loop and fans events out; it calls into this core but the core
never imports the shell's machinery. The 1e6 distribution gate
(``tests/test_crash_loop.py``) drives THIS module directly, so the test guards the
production path, not a re-derivation.

Fairness (per-round signature, spec §A.4 — distinct from the per-user Originals
signature): one float ``f`` per round is drawn as
``f = create_rng(round_server_seed, client_seed=round_id, nonce=round_number).next()``
and mapped to ``C = crash_point(f, edge)``. This is byte-identical to the
verifier's ``reproduce_round(...)`` (both go through ``engine.rng.create_rng`` →
``next``), so server == verifier by construction. ``crash_point`` is the S10
carry-forward (``engine.games._curve``), reused VERBATIM — never reimplemented.

Commit/reveal: ``server_seed_hash`` (``engine.fairness.commit`` = SHA-256 of the
seed) is the commitment broadcast during WAITING; the raw ``round_server_seed`` is
revealed only at CRASH and MUST hash back to that commitment.

C is fixed at round open from the committed seed; the shell's cosmetic multiplier
tick NEVER feeds back into C (no client input, no wall-clock value influences C).

Seed *generation* (CSPRNG via ``secrets``) lives in the shell (app), never here.

S19 NOTE — persistence is a straight mirror of this tuple, written at round START
(seed committed before the outcome is known): persist
``(round_id, round_number, round_server_seed, server_seed_hash, C, status)``. Reuse
the existing ``GameRound`` (a MULTIPLAYER discriminator) + a nullable per-round
seed column (or JSONB) rather than a parallel crash-only table; re-settle = re-run
``CrashRound.open(...)`` from the persisted seed. S18 persists NOTHING (no bet yet).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from engine.fairness import commit
from engine.games._curve import crash_point
from engine.rng import create_rng


class RoundStatus(StrEnum):
    """The Crash round lifecycle. Transitions are strictly ordered (see below)."""

    WAITING = "WAITING"  # commit broadcast; accepting nothing yet (bets land in S19)
    LOCKED = "LOCKED"  # betting window closed; about to run
    RUNNING = "RUNNING"  # cosmetic multiplier climbing toward the fixed C
    CRASHED = "CRASHED"  # reached C; seed revealed
    SETTLED = "SETTLED"  # round closed (settlement lands in S19)


# The only legal forward transitions; any other move raises InvalidTransition.
_NEXT: dict[RoundStatus, RoundStatus] = {
    RoundStatus.WAITING: RoundStatus.LOCKED,
    RoundStatus.LOCKED: RoundStatus.RUNNING,
    RoundStatus.RUNNING: RoundStatus.CRASHED,
    RoundStatus.CRASHED: RoundStatus.SETTLED,
}


class InvalidTransition(RuntimeError):
    """Raised when a round is asked to make an out-of-order state transition."""


def crash_point_for_round(
    round_server_seed: bytes, round_id: str, round_number: int, edge: float
) -> float:
    """Compute the round's fixed crash multiplier ``C`` from the committed seed.

    Draws the single per-round ``f`` (the §A.4 signature: clientSeed = roundId,
    nonce = roundNumber, cursor = 0) and maps it through the shared S10 curve.
    Byte-identical to ``verifier.reproduce_round`` followed by ``crash_point``.
    """
    f = create_rng(round_server_seed, client_seed=round_id, nonce=round_number).next()
    return crash_point(f, edge)


@dataclass(frozen=True)
class CrashRound:
    """An authoritative Crash round: identity + fixed outcome + lifecycle state.

    Frozen — every transition returns a NEW instance, so a caller can never mutate
    a round into an illegal state and the actor's history is a value sequence.
    """

    round_server_seed: bytes
    round_id: str
    round_number: int
    C: float
    status: RoundStatus

    @classmethod
    def open(
        cls,
        *,
        round_server_seed: bytes,
        round_id: str,
        round_number: int,
        edge: float,
    ) -> CrashRound:
        """Open a round in WAITING with ``C`` fixed from the committed seed."""
        return cls(
            round_server_seed=round_server_seed,
            round_id=round_id,
            round_number=round_number,
            C=crash_point_for_round(round_server_seed, round_id, round_number, edge),
            status=RoundStatus.WAITING,
        )

    @property
    def server_seed_hash(self) -> str:
        """The pre-reveal commitment SHA-256(round_server_seed) (``engine.fairness``)."""
        return commit(self.round_server_seed)

    def _transition(self, expected: RoundStatus) -> CrashRound:
        if self.status is not expected:
            raise InvalidTransition(
                f"cannot advance from {self.status.value}: expected {expected.value}"
            )
        return replace(self, status=_NEXT[expected])

    def lock(self) -> CrashRound:
        """WAITING → LOCKED (close the betting window)."""
        return self._transition(RoundStatus.WAITING)

    def start(self) -> CrashRound:
        """LOCKED → RUNNING (begin the cosmetic climb)."""
        return self._transition(RoundStatus.LOCKED)

    def crash(self) -> CrashRound:
        """RUNNING → CRASHED (reached C; the shell reveals the seed here)."""
        return self._transition(RoundStatus.RUNNING)

    def settle(self) -> CrashRound:
        """CRASHED → SETTLED (round closed)."""
        return self._transition(RoundStatus.CRASHED)


__all__ = [
    "CrashRound",
    "InvalidTransition",
    "RoundStatus",
    "crash_point_for_round",
]
