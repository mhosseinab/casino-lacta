"""Shared engine contracts — pure, framework-free Protocols + value objects.

The consumer-shaped seams every game conforms to. `engine/` depends on these
abstractions, never on a concrete RNG, framework, or store (DIP). Build-plan_v2 §3.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class InvalidBetInput(ValueError):
    """Game-specific bet input is out of range or malformed.

    Raised by :meth:`InstantGame.validate_input` at the bet-loop boundary. Raising
    a deterministic ``ValueError`` on bad input is a PURE rejection (no IO/clock/
    random), so it belongs in the engine; ``app/`` maps it to a 4xx at the
    transport edge. Subclasses ``ValueError`` so generic input handling still
    catches it.
    """


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

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """The per-game input fence — pure, run at the bet-loop boundary BEFORE any
        nonce reservation, debit, or :meth:`play`.

        Raise :class:`InvalidBetInput` on out-of-range or malformed game-specific
        input; return ``None`` when valid. Each game implements this explicitly
        (OCP) — there is no protocol-level default, so a new game cannot silently
        inherit a no-op fence. A game with no constrained input still conforms with
        an empty body. Pure: stdlib + engine only, no IO/clock/random.
        """
        ...

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve a bet to an Outcome — pure function of (input, rng stream, cfg)."""
        ...


class StatefulGame(Protocol):
    """Multi-step games with server-held state (Mines, HiLo, Crash-bet, Blackjack, Video Poker).

    The state returned by :meth:`init` and advanced by :meth:`step` is OPAQUE to the
    app: the bet loop persists it and feeds it back, never inspecting its game-specific
    keys (redaction lives here, not in the app). The client-facing projection a step
    surfaces is its ``Outcome.detail`` — which carries a generic ``"status"`` in
    ``{ACTIVE, CASHED_OUT, LOST}`` (and ``Outcome.multiplier`` on settlement) so the
    app drives credit/round-status generically, knowing nothing of the game's rules.
    """

    id: str

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """The per-round input fence — pure, run at the bet-loop boundary BEFORE the
        one-active-round guard, any nonce reservation, debit, or :meth:`init`.

        Raise :class:`InvalidBetInput` on out-of-range or malformed round-open input
        (e.g. Mines' mine count); return ``None`` when valid. Each stateful game
        implements this explicitly (OCP) — there is no protocol-level default, so a
        new game cannot silently inherit a no-op fence. Pure: stdlib + engine only,
        no IO/clock/random. Mirrors :meth:`InstantGame.validate_input`.
        """
        ...

    def init(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> dict[str, Any]:
        """Commit the hidden layout from the rng stream at round start; return opaque state."""
        ...

    def public_view(self, state: dict[str, Any]) -> dict[str, Any]:
        """The CLIENT-SAFE projection of the opaque game state — the game's own redaction,
        in ONE auditable place.

        A pure function (stdlib + engine only, no IO/clock/random) the APP calls to render
        a round snapshot at ``/bet`` open and ``/state`` resume: the game decides what is
        public so the app never indexes a key inside the opaque state (the redaction seam
        lives here, not in the app). While the round is ACTIVE it MUST withhold any hidden
        information (Mines' unrevealed cells); at a TERMINAL status it MAY disclose it
        (provable fairness). Each stateful game implements this explicitly (OCP) — no
        protocol-level default, so a new game cannot silently inherit a leaky view.

        Purely app-facing: the verifier / determinism path NEVER calls it (it reproduces
        outcomes via ``init``/``step``), so reproduction stays independent of presentation.
        """
        ...

    def step(
        self, state: dict[str, Any], action: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome | None]:
        """Advance state by one action; return (next_state, Outcome|None) — None until resolved.

        ``rng`` is injected per step — SYMMETRIC to :meth:`init` — so a game that draws
        fresh entropy on each action (HiLo's next card) consumes it here, exactly as
        ``init`` consumes the committed round-start draws. A game whose randomness is
        fully committed at ``init`` (Mines) simply ignores ``rng`` and its stream cursor
        does not advance. Entropy enters outcome logic ONLY through this seam — never
        ``random``/``secrets`` — so the verifier reproduces the full step sequence by
        driving the SAME stream forward through the SAME actions, and the app resumes a
        mid-round stream purely from ``(serverSeed, clientSeed, nonce, cursor)``.
        """
        ...
