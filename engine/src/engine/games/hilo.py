"""``originals.hilo`` — the provably-fair HiLo Original (spec §A.7), the SECOND
``StatefulGame`` and the first to draw FRESH entropy per action.

A card is shown; the player predicts whether the next card is **Higher-or-same**
(``HIGHER``) or **Lower-or-same** (``LOWER``). A correct call compounds the
multiplier and the revealed card becomes the new shown card; the player may cash
out any time. A wrong call ends the round with no payout (spec §A.7):

    p(Higher-or-same) = (14 - r) / 13     # for shown rank r ∈ 1..13
    p(Lower-or-same)  = r / 13
    stepMultiplier    = (1 - edge) / p(chosen side)
    cumulative        = Π stepMultipliers   # over the won steps
    EV(each step)     = p · stepMultiplier = 1 - edge   # exact, pre-rounding

A **tie counts as a win for EITHER chosen side** — that is why the two side
probabilities sum to ``> 1`` (the tie rank is double-counted). Pricing each side
by its own ``p`` keeps every step's EV at ``1 - edge``.

**RNG mapping (documented).** Each next card draws ONE float from the injected
stream: ``cardIndex = floor(f * 52)`` (one of 52 cards), then ``rank =
cardIndex // 4 + 1`` — a UNIFORM map onto 1..13 (each rank owns exactly 4 of the
52 indices, i.e. its four suits). Draws are **independent / with replacement**
(a fresh ``floor(f*52)`` each time, NOT sampleWithoutReplacement) — suit is
discarded, only rank matters. The first shown card is drawn at :meth:`init`
(cursor → 1); every guess draws the next card at :meth:`step` from the SAME
counter-indexed stream (cursor advances by 1 per guess), so the whole sequence is
a pure function of ``(serverSeed, clientSeed, nonce, cursor)`` and the verifier
reproduces it by driving the same stream through the same actions.

**No secret state.** The entire game state — shown card, cumulative multiplier,
status — is already shown to the player, so nothing is hidden (unlike Mines'
layout). Unpredictability comes ONLY from the unrevealed server seed; HiLo never
stashes a seed or a future card in its state.

State machine: ``ACTIVE(guess → reveal → …) → {CASHED_OUT | LOST}``. Cash-out
requires ≥1 successful guess (mirrors Mines' "cashout requires ≥1 reveal").

**Rounding.** The engine returns ``cumulative`` as an exact float product; money
is floored ONCE at settlement (``app`` floors ``stake × cumulative`` at cashout,
then caps to ``max_win``). The per-step floor is never repeated, so realized RTP
stays ``≤`` target.

Pure: stdlib only (``math.floor``); entropy enters solely via the injected
``RngStream``; ``edge`` is read from ``cfg``.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

_DECK = 52
_RANKS = 13  # A(1) … K(13)
_HIGHER = "HIGHER"
_LOWER = "LOWER"
_SIDES = (_HIGHER, _LOWER)

# Round status literals — the generic stateful-game settlement signal carried in
# Outcome.detail["status"] (the app reads this, never the opaque state internals).
_ACTIVE = "ACTIVE"
_CASHED_OUT = "CASHED_OUT"
_LOST = "LOST"


def draw_rank(rng: RngStream) -> int:
    """Draw one card and return its rank 1..13: ``floor(f*52)//4 + 1`` (uniform)."""
    return floor(rng.next() * _DECK) // 4 + 1


def p_win(rank: int, side: str) -> float:
    """``p(Higher-or-same)=(14-r)/13`` / ``p(Lower-or-same)=r/13`` (tie wins either side)."""
    if side == _HIGHER:
        return (14 - rank) / _RANKS
    return rank / _RANKS


def step_multiplier(rank: int, side: str, edge: float) -> float:
    """``(1 - edge) / p(chosen side)`` — the step multiplier for shown rank ``rank``."""
    return (1.0 - edge) / p_win(rank, side)


def _is_win(shown: int, revealed: int, side: str) -> bool:
    """Higher-or-same wins on ``revealed >= shown``; Lower-or-same on ``revealed <= shown``
    (a tie, ``revealed == shown``, satisfies both — it wins for whichever side was chosen)."""
    if side == _HIGHER:
        return revealed >= shown
    return revealed <= shown


@dataclass(frozen=True)
class HiLo:
    """Conforms to ``engine.types.StatefulGame`` (spec §A.7)."""

    id: str = "originals.hilo"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """HiLo has NO game-specific round-open input to fence (the only client input is
        the per-step ``side``, validated in :meth:`step`). The stake/currency are fenced
        by the shared bet loop. Conforms to the protocol with an explicit no-op so a new
        game cannot silently inherit a missing fence."""

    def init(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> dict[str, Any]:
        """Draw the first shown card from the stream (cursor → 1); return opaque state.

        ``edge`` is snapshotted into the state so :meth:`step` prices each guess from the
        state alone (the round's config is pinned at open)."""
        return {
            "status": _ACTIVE,
            "shown_rank": draw_rank(rng),
            "cumulative": 1.0,
            "steps": 0,
            "edge": float(cfg.edge),
        }

    def step(
        self, state: dict[str, Any], action: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome | None]:
        """Advance the round by one action; return ``(next_state, Outcome)``.

        A ``guess`` draws the next card from the injected stream (cursor advances by 1);
        a ``cashout`` settles the cumulative multiplier. ``detail["status"]`` is the
        generic settlement signal the app reads to drive credit/round-status."""
        if state["status"] != _ACTIVE:
            raise InvalidBetInput(f"round is terminal ({state['status']}); no further actions")

        op = action.get("op")
        if op == "cashout":
            return self._cashout(state)
        if op == "guess":
            return self._guess(state, action, rng)
        raise InvalidBetInput(f"unknown hilo op {op!r}")

    # -- transitions ----------------------------------------------------------

    def _guess(
        self, state: dict[str, Any], action: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome]:
        side = action.get("side")
        if side not in _SIDES:
            raise InvalidBetInput(f"hilo guess 'side' must be one of {_SIDES}, got {side!r}")

        shown = int(state["shown_rank"])
        edge = float(state["edge"])
        revealed = draw_rank(rng)  # consumes exactly one draw — the next card
        won = _is_win(shown, revealed, side)

        if not won:
            next_state = {**state, "status": _LOST}
            return next_state, Outcome(
                multiplier=0.0,
                detail={
                    "status": _LOST,
                    "side": side,
                    "guessedFromRank": shown,
                    "revealedRank": revealed,
                    "won": False,
                    "steps": int(state["steps"]),
                },
            )

        sm = step_multiplier(shown, side, edge)
        cumulative = float(state["cumulative"]) * sm
        steps = int(state["steps"]) + 1
        next_state = {
            **state,
            "shown_rank": revealed,  # the revealed card is now the shown card
            "cumulative": cumulative,
            "steps": steps,
        }
        return next_state, Outcome(
            multiplier=cumulative,
            detail={
                "status": _ACTIVE,
                "side": side,
                "guessedFromRank": shown,
                "revealedRank": revealed,
                "shownRank": revealed,  # next card to guess on
                "won": True,
                "stepMultiplier": sm,
                "currentMultiplier": cumulative,
                "steps": steps,
            },
        )

    def public_view(self, state: dict[str, Any]) -> dict[str, Any]:
        """The client-safe round snapshot (for ``/bet`` open + ``/state`` resume).

        HiLo holds NO secret state — the shown card, cumulative multiplier and status are
        all already known to the player — so the safe projection IS the whole public
        state. ``shownRank`` is the card the next guess is made against (this is what makes
        the opening playable). Nothing is withheld at any status."""
        return {
            "status": str(state["status"]),
            "shownRank": int(state["shown_rank"]),
            "currentMultiplier": float(state["cumulative"]),
            "steps": int(state["steps"]),
        }

    def _cashout(self, state: dict[str, Any]) -> tuple[dict[str, Any], Outcome]:
        steps = int(state["steps"])
        if steps < 1:
            raise InvalidBetInput("hilo cashout requires at least one successful guess")
        cumulative = float(state["cumulative"])
        next_state = {**state, "status": _CASHED_OUT}
        return next_state, Outcome(
            multiplier=cumulative,
            detail={
                "status": _CASHED_OUT,
                "multiplier": cumulative,
                "currentMultiplier": cumulative,
                "steps": steps,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = HiLo()
