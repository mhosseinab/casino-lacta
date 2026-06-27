"""``originals.dice`` — the canonical provably-fair dice Original (spec §A.1).

Pick a ``target`` and a ``direction``; one draw from the seeded stream maps to
``roll = floor(f * 10000) / 100`` ∈ [0.00, 99.99] (two-decimal resolution).
``UNDER`` wins when ``roll < target`` (win probability ``p = target / 100``);
``OVER`` wins when ``roll > target`` (``p = (100 - target) / 100``). A win pays
``multiplier = (1 - edge) / p`` so ``RTP = p · multiplier = 1 - edge``; a loss
pays ``0``.

Pure: stdlib only (``math``), entropy enters solely via the injected
``RngStream``; ``edge`` is read from ``cfg`` (never a literal). The same
``(server_seed, client_seed, nonce)`` + ``input`` always yields the same roll —
server == verifier by construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

# One draw → a roll in [0.00, 99.99]: ``floor(f * 10000)`` yields an integer in
# {0, …, 9999}, scaled by 1/100 to two-decimal resolution (spec §A.1 RNG mapping).
_ROLL_QUANTA = 10_000
_ROLL_SCALE = 100

_DIRECTION_OVER = "OVER"
_DIRECTION_UNDER = "UNDER"

# Valid win-probability band (spec §A.1 "Edge cases"): reject targets that push
# ``p`` outside ``[0.0001, 0.98]`` — both extremes (a near-certain or near-
# impossible bet) are disallowed regardless of direction.
_P_MIN = 0.0001
_P_MAX = 0.98


@dataclass(frozen=True)
class Dice:
    """Conforms to ``engine.types.InstantGame`` (spec §A.1)."""

    id: str = "originals.dice"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / out-of-band dice input BEFORE any money moves (pure).

        ``target`` must be present and numeric; ``direction`` (default ``UNDER``)
        must be ``UNDER`` or ``OVER``; and the resulting win probability ``p`` must
        lie in ``[0.0001, 0.98]`` (spec §A.1). This is the COMPLETE fence — passing
        it guarantees :meth:`play` cannot raise on this input.
        """
        if "target" not in input:
            raise InvalidBetInput("dice requires a 'target'")
        try:
            target = float(input["target"])
        except (TypeError, ValueError) as exc:
            raise InvalidBetInput(
                f"dice 'target' must be numeric, got {input['target']!r}"
            ) from exc
        direction = str(input.get("direction", _DIRECTION_UNDER)).upper()
        if direction not in (_DIRECTION_UNDER, _DIRECTION_OVER):
            raise InvalidBetInput(
                f"dice 'direction' must be UNDER or OVER, got {direction!r}"
            )
        p = (
            (100.0 - target) / 100.0
            if direction == _DIRECTION_OVER
            else target / 100.0
        )
        if not (_P_MIN <= p <= _P_MAX):
            raise InvalidBetInput(
                f"dice target {target} ({direction}) yields win probability {p} "
                f"outside [{_P_MIN}, {_P_MAX}]"
            )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one dice bet from a single draw — pure function of (input, rng, cfg).

        ``input`` carries ``{"target": float, "direction": "UNDER"|"OVER"}``;
        ``direction`` defaults to ``UNDER``. The win probability ``p`` is
        ``target/100`` (UNDER) or ``(100-target)/100`` (OVER); the win multiplier
        is ``(1 - edge) / p``. When ``p == 0`` the winning region is empty, so the
        bet always loses and the division is never reached (no guard needed).
        """
        target = float(input["target"])
        direction = str(input.get("direction", _DIRECTION_UNDER)).upper()

        roll = math.floor(rng.next() * _ROLL_QUANTA) / _ROLL_SCALE

        if direction == _DIRECTION_OVER:
            won = roll > target
            win_probability = (100.0 - target) / 100.0
        else:  # UNDER (the default)
            won = roll < target
            win_probability = target / 100.0

        multiplier = (1.0 - cfg.edge) / win_probability if won else 0.0
        return Outcome(
            multiplier=multiplier,
            detail={
                "roll": roll,
                "target": target,
                "direction": direction,
                "won": won,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = Dice()
