"""``stub.coinflip`` — a trivial instant game that exercises the shared bet loop.

A deliberately minimal test fixture (NOT a shipped Original — the user-facing
``originals.coinflip`` lands in S17 and coexists with this by design). One draw
from the seeded stream decides a fair 50/50; a win pays ``1.98x`` (so the
expected return is ``0.5 * 1.98 = 0.99`` → a 1% house edge), a loss pays nothing.

Pure: stdlib only, entropy enters solely via the injected ``RngStream`` (the
``cfg`` win multiplier is read from config, never a literal money amount). The
same ``(server_seed, client_seed, nonce)`` always yields the same flip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.types import GameConfig, Outcome, RngStream

# Default win multiplier when config carries none (config is authoritative at runtime).
_DEFAULT_WIN_MULTIPLIER = 1.98


@dataclass(frozen=True)
class StubCoinflip:
    """Conforms to ``engine.types.InstantGame``."""

    id: str = "stub.coinflip"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """No constrained input: a fair coin accepts any (or no) ``side``. Explicit
        empty body conforms to the ``InstantGame`` fence seam (OCP)."""

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one fair coin flip from a single draw.

        ``input`` may carry ``{"side": "heads"|"tails"}`` (cosmetic — a fair coin
        makes the chosen side irrelevant to the edge); the draw alone decides.
        """
        win_multiplier = float(cfg.params.get("win_multiplier", _DEFAULT_WIN_MULTIPLIER))
        roll = rng.next()
        landed = "heads" if roll < 0.5 else "tails"
        side = str(input.get("side", "heads"))
        won = landed == side
        return Outcome(
            multiplier=win_multiplier if won else 0.0,
            detail={"roll": roll, "landed": landed, "side": side, "won": won},
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = StubCoinflip()
