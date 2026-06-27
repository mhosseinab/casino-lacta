"""The shared RTP-as-CI gate harness — ``run_rtp`` measures UNCAPPED engine math.

``run_rtp(game, input, n, target, tol)`` runs ``n`` independent trials over a
seeded stream and asserts the measured RTP is within ``tol`` of ``target``. It
reuses ``engine`` verbatim (one ``create_rng`` per trial → the game's ``play``),
so the gate measures the SAME outcome math the server settles — there is no
re-implementation here.

Two rules the step pins down:

* **Uncapped.** RTP is the mean payout *multiplier* across trials — the pure
  engine EV, with NO ``maxWin`` cap and NO floor-rounding applied. Cap/rounding
  behaviour is asserted by separate money unit tests; mixing them in would let a
  binding cap silently mask a wrong paytable. Callers therefore pick
  stake/maxWin so ``top_multiplier * stake <= maxWin`` when they wire the
  surrounding bet (limits never bind the gate).
* **Tolerance is a CLT half-width, never a flat ±0.5%.** When ``tol`` is None the
  default is ``z * sqrt(Var/n)`` — the ``z``-sigma confidence half-width of the
  estimated mean, with ``Var`` the measured sample variance of the per-trial
  multiplier and ``z = _Z`` (a wide, fixed 5-sigma gate so a correct game effectively
  never flakes: P(|Z|>5) ≈ 6e-7, deterministic under the fixed seed anyway). The
  gate NARROWS as ``n`` grows — the iron rule "never widen a tolerance to pass"
  is structural: you cannot loosen it without lowering ``z`` (a visible change).

``numpy``-vectorized sims are permitted in ``tests/`` only (never ``engine/``);
this harness stays pure-Python so it reuses the engine stream for parity. Heavy
games that need vectorization (slots, S22) build it in their own test, never here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InstantGame

# Sigma multiplier for the default confidence half-width. 5 sigma → a ~6e-7
# two-sided false-reject rate; with a fixed seed the run is deterministic so even
# that never materialises. Wide enough to be stable, tight enough to catch a
# mis-tuned paytable (a 0.5-1% EV error dwarfs a 5-sigma window at n>=1e6).
_Z = 5.0

# A fixed harness seed: the gate is a deterministic function of (game, input, n),
# so a passing run reproduces exactly — no RNG-driven flakes.
_HARNESS_SERVER_SEED = b"rtp-harness-server-seed"
_HARNESS_CLIENT_SEED = "rtp-harness"


@dataclass(frozen=True)
class RtpResult:
    """The outcome of an RTP measurement (returned for visibility / logging)."""

    rtp: float
    target: float
    tol: float
    n: int
    variance: float


def run_rtp(
    game: str | InstantGame,
    input: dict[str, Any],
    n: int,
    target: float,
    tol: float | None = None,
    *,
    cfg: GameConfig | None = None,
    z: float = _Z,
) -> RtpResult:
    """Measure RTP over ``n`` seeded trials; assert ``|rtp - target| <= tol``.

    ``game`` is a registry id (loaded via the engine registry seam) or an
    ``InstantGame`` instance. ``cfg`` defaults to the engine registry default for
    the game. ``tol`` defaults to the CLT half-width ``z * sqrt(Var/n)`` measured
    from the sample — pass an explicit ``tol`` only with a documented derivation,
    NEVER to widen a failing gate.
    """
    if n <= 1:
        raise ValueError("run_rtp needs n > 1 to estimate a variance")
    instant: InstantGame = load_game(game) if isinstance(game, str) else game  # type: ignore[assignment]
    game_id = game if isinstance(game, str) else instant.id
    config = cfg if cfg is not None else default_config(game_id)

    total = 0.0
    total_sq = 0.0
    for nonce in range(n):
        rng = create_rng(_HARNESS_SERVER_SEED, _HARNESS_CLIENT_SEED, nonce)
        multiplier = instant.play(dict(input), rng, config).multiplier
        total += multiplier
        total_sq += multiplier * multiplier

    mean = total / n
    # Population variance of the sampled multipliers (E[X^2] - E[X]^2), clamped at
    # 0 against tiny negative float error for a degenerate (constant) game.
    variance = max(total_sq / n - mean * mean, 0.0)
    half_width = z * math.sqrt(variance / n) if tol is None else tol

    assert abs(mean - target) <= half_width, (
        f"RTP {mean:.6f} for {game_id} deviates from target {target:.6f} by "
        f"{abs(mean - target):.6f} > tol {half_width:.6f} "
        f"(n={n}, var={variance:.6f}, z={z})"
    )
    return RtpResult(rtp=mean, target=target, tol=half_width, n=n, variance=variance)
