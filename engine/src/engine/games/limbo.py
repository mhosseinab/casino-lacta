"""``originals.limbo`` — the provably-fair Limbo Original (spec §A.3).

Pick a ``target`` multiplier; one draw from the seeded stream maps to a generated
multiplier ``X = crash_point(f, edge)`` (the shared §2.4 curve, reused VERBATIM by
Crash S18). The bet wins when ``X >= target`` and pays ``multiplier = target``;
otherwise it pays ``0``. Since ``P(X >= target) = (1 - edge) / target``, the RTP
is ``target · (1 - edge) / target = 1 - edge`` for every target.

Pure: stdlib only (the curve helper imports ``math``); entropy enters solely via
the injected ``RngStream``; ``edge`` is read from ``cfg`` (never a literal). The
same ``(server_seed, client_seed, nonce)`` + ``input`` always yields the same
generated ``X`` — server == verifier by construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from engine.games._curve import crash_point
from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

# The smallest valid target: the §2.4 curve floors every generated ``X`` at 1.00×,
# so a target ≤ 1.00 is a guaranteed win (not a gamble) and the tail identity
# ``P(X >= x) = (1 - edge)/x`` only holds strictly ABOVE the floor. 1.01 is the
# smallest two-decimal multiplier above it (spec §A.3). The maxMultiplier ceiling
# is a per-currency limits/bet-loop concern (§2.5), not this structural fence.
_TARGET_MIN = 1.01


def _target(input: dict[str, Any]) -> float:
    """Read the target multiplier from ``input``, accepting ``target`` or the
    API-envelope alias ``targetMultiplier`` (spec §A.3 request body)."""
    if "target" in input:
        return float(input["target"])
    return float(input["targetMultiplier"])


@dataclass(frozen=True)
class Limbo:
    """Conforms to ``engine.types.InstantGame`` (spec §A.3)."""

    id: str = "originals.limbo"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / out-of-band limbo input BEFORE any money moves (pure).

        ``target`` (alias ``targetMultiplier``) must be present, numeric, and at
        least ``_TARGET_MIN`` (1.01×). This is the COMPLETE fence — passing it
        guarantees :meth:`play` cannot raise on this input.
        """
        if "target" not in input and "targetMultiplier" not in input:
            raise InvalidBetInput("limbo requires a 'target' (or 'targetMultiplier')")
        raw = input["target"] if "target" in input else input["targetMultiplier"]
        try:
            target = float(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidBetInput(
                f"limbo 'target' must be numeric, got {raw!r}"
            ) from exc
        if not math.isfinite(target):
            raise InvalidBetInput(f"limbo 'target' must be finite, got {target}")
        if target < _TARGET_MIN:
            raise InvalidBetInput(
                f"limbo 'target' must be >= {_TARGET_MIN}, got {target}"
            )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one limbo bet from a single draw — pure function of (input, rng, cfg).

        Consumes exactly ONE ``rng.next()`` → ``f``, maps it through the shared
        ``crash_point`` curve to the generated multiplier ``X``, and wins when
        ``X >= target`` (paying ``multiplier = target``) else loses (``0``).
        """
        target = _target(input)

        generated = crash_point(rng.next(), cfg.edge)
        won = generated >= target
        multiplier = target if won else 0.0

        return Outcome(
            multiplier=multiplier,
            detail={
                "generated": generated,
                "target": target,
                "won": won,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = Limbo()
