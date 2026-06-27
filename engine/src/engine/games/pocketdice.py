"""``originals.pocketdice`` — the provably-fair Pocket Dice Original (spec §A.2).

Bet on the **sum of 2d6** (2–12): pick an integer ``target`` and a ``direction``.
Two draws from the seeded stream map to ``die = floor(f*6)+1`` each, summed to
``s ∈ [2, 12]`` — the triangular 2d6 distribution (pmf out of 36:
2→1, 3→2, …, 7→6, …, 12→1), distinct from Dice's uniform roll. ``UNDER`` wins
when ``s < target``, ``OVER`` when ``s > target``. A win pays
``multiplier = (1 - edge) / p`` where ``p`` is the TRUE probability of the winning
region (summed from the pmf counts), so ``RTP = p · (1-edge)/p = 1 - edge`` for
every target/direction; a loss pays ``0``.

Pure: stdlib only (``math``), entropy enters solely via the injected
``RngStream``; ``edge`` is read from ``cfg`` (never a literal). The same
``(server_seed, client_seed, nonce)`` + ``input`` always yields the same two dice
— server == verifier by construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

# The 2d6 probability mass function: counts out of 36 for each achievable sum
# (spec §A.2). The win probability of any region is the sum of its counts / 36 —
# computed structurally so a target's payout is never a hardcoded paytable literal.
_PMF_COUNTS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
_OUTCOMES = 36

# Each die: floor(f*6)+1 ∈ {1..6} for f ∈ [0,1) (spec §A.2 RNG mapping).
_DIE_FACES = 6

# The achievable 2d6 sum band; a target outside it is structurally invalid.
_SUM_MIN = 2
_SUM_MAX = 12

_DIRECTION_OVER = "OVER"
_DIRECTION_UNDER = "UNDER"


def _win_probability(target: int, direction: str) -> float:
    """P(win) for an integer ``target`` and ``direction``, from the 2d6 pmf.

    ``OVER`` wins on ``sum > target``; ``UNDER`` on ``sum < target``. Returns the
    summed pmf mass of the winning region (0.0 when that region is empty, e.g.
    ``OVER 12`` / ``UNDER 2``).
    """
    if direction == _DIRECTION_OVER:
        count = sum(c for s, c in _PMF_COUNTS.items() if s > target)
    else:  # UNDER
        count = sum(c for s, c in _PMF_COUNTS.items() if s < target)
    return count / _OUTCOMES


@dataclass(frozen=True)
class PocketDice:
    """Conforms to ``engine.types.InstantGame`` (spec §A.2)."""

    id: str = "originals.pocketdice"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / out-of-band pocket-dice input BEFORE any money moves (pure).

        ``target`` must be present, numeric, finite, and a whole number in the
        achievable 2d6 band ``[2, 12]``; ``direction`` (default ``UNDER``) must be
        ``UNDER`` or ``OVER``; and the winning region must be non-empty
        (``p > 0`` — this rejects ``OVER 12`` / ``UNDER 2`` structurally). This is
        the COMPLETE fence — passing it guarantees :meth:`play` cannot raise on
        this input.
        """
        if "target" not in input:
            raise InvalidBetInput("pocketdice requires a 'target'")
        try:
            target = float(input["target"])
        except (TypeError, ValueError) as exc:
            raise InvalidBetInput(
                f"pocketdice 'target' must be numeric, got {input['target']!r}"
            ) from exc
        if not math.isfinite(target):
            raise InvalidBetInput(f"pocketdice 'target' must be finite, got {target}")
        if not target.is_integer():
            raise InvalidBetInput(
                f"pocketdice 'target' must be a whole 2d6 sum, got {target}"
            )
        direction = str(input.get("direction", _DIRECTION_UNDER)).upper()
        if direction not in (_DIRECTION_UNDER, _DIRECTION_OVER):
            raise InvalidBetInput(
                f"pocketdice 'direction' must be UNDER or OVER, got {direction!r}"
            )
        target_int = int(target)
        if not (_SUM_MIN <= target_int <= _SUM_MAX):
            raise InvalidBetInput(
                f"pocketdice 'target' {target_int} outside achievable 2d6 band "
                f"[{_SUM_MIN}, {_SUM_MAX}]"
            )
        if _win_probability(target_int, direction) <= 0.0:
            raise InvalidBetInput(
                f"pocketdice 'target' {target_int} ({direction}) has zero win "
                f"probability (no 2d6 sum can satisfy it)"
            )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one pocket-dice bet from two draws — pure function of (input, rng, cfg).

        ``input`` carries ``{"target": int, "direction": "UNDER"|"OVER"}``;
        ``direction`` defaults to ``UNDER``. Consumes exactly TWO ``rng.next()``
        draws → two dice → their sum. The win multiplier is ``(1 - edge) / p`` with
        ``p`` the true probability of the winning region; on a win ``p > 0`` is
        guaranteed by the fence, so the division is always safe.
        """
        target = int(float(input["target"]))
        direction = str(input.get("direction", _DIRECTION_UNDER)).upper()

        d1 = math.floor(rng.next() * _DIE_FACES) + 1
        d2 = math.floor(rng.next() * _DIE_FACES) + 1
        total = d1 + d2

        won = total > target if direction == _DIRECTION_OVER else total < target
        multiplier = (
            (1.0 - cfg.edge) / _win_probability(target, direction) if won else 0.0
        )
        return Outcome(
            multiplier=multiplier,
            detail={
                "dice": [d1, d2],
                "sum": total,
                "target": target,
                "direction": direction,
                "won": won,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = PocketDice()
