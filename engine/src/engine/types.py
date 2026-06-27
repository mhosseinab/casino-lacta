"""Shared engine contracts — pure, framework-free Protocols + value objects.

The consumer-shaped seams every game conforms to. `engine/` depends on these
abstractions, never on a concrete RNG, framework, or store (DIP). Build-plan_v2 §3.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class RngStream(Protocol):
    """Deterministic uniform stream. Entropy enters outcome logic ONLY via this seam."""

    def next(self) -> float:
        """Return a uniform float in [0, 1), advancing the deterministic stream."""
        ...


@dataclass(frozen=True)
class GameConfig:
    """Per-game policy (RTP edge + game-specific params). Authoritative copy is config, not code."""

    edge: float
    params: dict[str, Any]


@dataclass(frozen=True)
class Outcome:
    """Pure outcome of a bet. Payout in minor units is computed from `multiplier` by money.py."""

    multiplier: float
    detail: dict[str, Any]


class InstantGame(Protocol):
    """One-shot games resolved by a single pure call (Dice, Limbo, Keno, Roulette, slots, ...)."""

    id: str

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve a bet to an Outcome — pure function of (input, rng stream, cfg)."""
        ...


class StatefulGame(Protocol):
    """Multi-step games with server-held state (Mines, HiLo, Crash-bet, Blackjack, Baccarat)."""

    id: str

    def init(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> dict[str, Any]:
        """Commit the hidden layout from the rng stream at round start; return opaque state."""
        ...

    def step(
        self, state: dict[str, Any], action: dict[str, Any]
    ) -> tuple[dict[str, Any], Outcome | None]:
        """Advance state by one action; return (next_state, Outcome|None) — None until resolved."""
        ...
