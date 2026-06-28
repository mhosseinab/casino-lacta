"""``originals.mines`` — the provably-fair Mines Original (spec §A.6), the FIRST
``StatefulGame``.

5×5 grid, ``N = 25`` cells, ``M`` hidden mines (``1 ≤ M ≤ 24``). The mine layout is
committed at ROUND START from the seeded stream — ``sampleWithoutReplacement(25, M)``
— BEFORE any reveal, so the outcome is fixed at ``init`` and a reveal merely looks up
whether a cell already held a mine (anti-cheat + provably fair). After ``k`` safe
reveals (spec §A.6):

    fairMultiplier(k) = C(25, k) / C(25 - M, k)
    payout(k)         = (1 - edge) · fairMultiplier(k)        # the settlement multiplier
    P(survive k)      = C(25 - M, k) / C(25, k) = 1 / fairMultiplier(k)
    EV(cash after k)  = P(survive k) · payout(k) = 1 - edge   # exact, any k, any M

State machine: ``ACTIVE(reveals…) → {CASHED_OUT | LOST}``. Cash-out requires ≥1
reveal. Reveal a mine → ``LOST`` (no credit). Re-revealing an already-revealed safe
cell is an intrinsic no-op (idempotent).

**The opaque-state + redaction seam.** ``init`` returns the FULL state (including
``mine_positions``); the app stores it in a server-only column and never inspects it.
A safe-reveal ``Outcome.detail`` carries ONLY public fields (``cell, safe, k,
currentMultiplier, nextMultiplier, status``) — never an unrevealed mine, so the
client-facing projection leaks nothing about where the remaining mines are. The full
layout (``minePositions``) is surfaced ONLY in a TERMINAL outcome (``LOST`` /
``CASHED_OUT``), when the round is over and disclosure is the point of provable
fairness.

Pure: stdlib only (``math.comb`` for the binomials); entropy enters solely via the
injected ``RngStream``. Same ``(server_seed, client_seed, nonce)`` + ``input`` always
commits the same layout — server == verifier by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Any

from engine.sampling import sample_without_replacement
from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

GRID_SIZE = 25  # 5×5
_MIN_MINES = 1
_MAX_MINES = 24  # at least one safe cell must remain

# Round status literals — the generic stateful-game settlement signal carried in
# Outcome.detail["status"] (the app reads this, never the opaque state internals).
_ACTIVE = "ACTIVE"
_CASHED_OUT = "CASHED_OUT"
_LOST = "LOST"


def _fair_multiplier(mines: int, k: int) -> float:
    """``C(25, k) / C(25 - M, k)`` — the fair (zero-edge) multiplier after k safe reveals."""
    return comb(GRID_SIZE, k) / comb(GRID_SIZE - mines, k)


def _payout_multiplier(mines: int, k: int, edge: float) -> float:
    """``(1 - edge) · fairMultiplier(k)`` — the engine's settlement multiplier (RTP = 1-edge)."""
    return (1.0 - edge) * _fair_multiplier(mines, k)


@dataclass(frozen=True)
class Mines:
    """Conforms to ``engine.types.StatefulGame`` (spec §A.6)."""

    id: str = "originals.mines"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed round-open input BEFORE any money moves (pure).

        ``mines`` must be an int in ``[1, 24]``. This is the COMPLETE fence — passing
        it guarantees :meth:`init` cannot raise on this input.
        """
        if "mines" not in input:
            raise InvalidBetInput("mines requires 'mines'")
        mines = input["mines"]
        # bool is a subclass of int — reject it so True/False can't masquerade as 1/0.
        if not isinstance(mines, int) or isinstance(mines, bool):
            raise InvalidBetInput(f"mines 'mines' must be an integer, got {mines!r}")
        if not (_MIN_MINES <= mines <= _MAX_MINES):
            raise InvalidBetInput(
                f"mines 'mines' must be in [{_MIN_MINES}, {_MAX_MINES}], got {mines}"
            )

    def init(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> dict[str, Any]:
        """Commit the mine layout from the stream at round start; return opaque state.

        Consumes exactly ``M`` draws (``sampleWithoutReplacement(25, M)``). ``edge`` is
        snapshotted into the state so :meth:`step` resolves the settlement multiplier
        from the state alone (the round's config is pinned at open)."""
        mines = int(input["mines"])
        positions = sample_without_replacement(rng, range(GRID_SIZE), mines)
        return {
            "status": _ACTIVE,
            "mines": mines,
            "grid_size": GRID_SIZE,
            "mine_positions": sorted(positions),
            "revealed": [],
            "edge": float(cfg.edge),
        }

    def step(
        self, state: dict[str, Any], action: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome | None]:
        """Advance the round by one action; return ``(next_state, Outcome)``.

        ``Outcome`` is always present for Mines (every action yields a client-facing
        projection in ``detail``). ``detail["status"]`` is the generic settlement
        signal the app reads to drive credit/round-status.

        ``rng`` is part of the :class:`~engine.types.StatefulGame` seam (symmetric to
        :meth:`init`) but Mines IGNORES it: the whole mine layout is committed at
        ``init``, so no action draws fresh entropy and the stream cursor stays at ``M``.
        A reveal merely looks up a pre-committed cell."""
        del rng  # Mines' randomness is fully committed at init (cursor unchanged).
        if state["status"] != _ACTIVE:
            raise InvalidBetInput(f"round is terminal ({state['status']}); no further actions")

        op = action.get("op")
        if op == "cashout":
            return self._cashout(state)
        if op == "reveal":
            return self._reveal(state, action)
        raise InvalidBetInput(f"unknown mines op {op!r}")

    # -- transitions ----------------------------------------------------------

    def _reveal(
        self, state: dict[str, Any], action: dict[str, Any]
    ) -> tuple[dict[str, Any], Outcome]:
        cell = action.get("cell")
        if not isinstance(cell, int) or isinstance(cell, bool):
            raise InvalidBetInput(f"mines reveal 'cell' must be an integer, got {cell!r}")
        if not (0 <= cell < GRID_SIZE):
            raise InvalidBetInput(f"mines reveal 'cell' {cell} out of range [0, {GRID_SIZE - 1}]")

        mines = int(state["mines"])
        edge = float(state["edge"])
        revealed: list[int] = list(state["revealed"])

        # Intrinsic idempotency: re-revealing an already-safe cell does not advance k.
        if cell in revealed:
            return state, self._safe_outcome(mines, len(revealed), cell, edge)

        if cell in set(state["mine_positions"]):
            next_state = {**state, "status": _LOST}
            return next_state, Outcome(
                multiplier=0.0,
                detail={
                    "cell": cell,
                    "safe": False,
                    "k": len(revealed),
                    "status": _LOST,
                    "minePositions": list(state["mine_positions"]),  # terminal disclosure
                },
            )

        revealed.append(cell)
        next_state = {**state, "revealed": revealed}
        return next_state, self._safe_outcome(mines, len(revealed), cell, edge)

    def _cashout(self, state: dict[str, Any]) -> tuple[dict[str, Any], Outcome]:
        k = len(state["revealed"])
        if k < 1:
            raise InvalidBetInput("mines cashout requires at least one reveal")
        mines = int(state["mines"])
        edge = float(state["edge"])
        multiplier = _payout_multiplier(mines, k, edge)
        next_state = {**state, "status": _CASHED_OUT}
        return next_state, Outcome(
            multiplier=multiplier,
            detail={
                "status": _CASHED_OUT,
                "k": k,
                "multiplier": multiplier,
                "minePositions": list(state["mine_positions"]),  # terminal disclosure
            },
        )

    @staticmethod
    def _safe_outcome(mines: int, k: int, cell: int, edge: float) -> Outcome:
        """The public projection of a safe reveal — NO unrevealed-cell information."""
        current = _payout_multiplier(mines, k, edge)
        # Next reveal is only possible while safe cells remain (k < 25 - M).
        next_mult = _payout_multiplier(mines, k + 1, edge) if k < GRID_SIZE - mines else None
        return Outcome(
            multiplier=current,
            detail={
                "cell": cell,
                "safe": True,
                "k": k,
                "currentMultiplier": current,
                "nextMultiplier": next_mult,
                "status": _ACTIVE,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = Mines()
