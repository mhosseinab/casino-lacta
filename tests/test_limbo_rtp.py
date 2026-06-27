"""S10 — limbo RTP-as-CI gate: measured RTP ≈ 1 - edge AND tail P(X≥x) ≈ (1-edge)/x.

Mirrors the dice gate split (``tests/test_dice_rtp.py``) so limbo is wired into the
dedicated ``rtp-gate`` CI job (CLAUDE.md DoD: "a game ships only when its RTP/EV
test is wired into that job"):

* ``test_*_fast`` — ~1e6 trials at targets {1.5, 2}, runs in the default PR
  ``pytest``. Both pay a low multiplier (1.5×/2×) so per-trial variance is ~0.5/1.0
  and the 5σ CLT band is tight (~±0.4%) at 1e6 — a meaningful gate on every PR.
  Higher targets (10×, 100×) have variance up to ~98, so their 1e6 band is too
  loose for a PR; they live in the heavy gate.
* ``test_*_heavy`` — 1e7 trials at targets {1.5, 2, 10, 100}, marked ``rtp_heavy``
  and EXCLUDED from the default run (``addopts = -m 'not rtp_heavy'``); the CI
  ``rtp-gate`` job runs it via ``-m rtp_heavy``.

Two independent measurements, neither with a hand-set tolerance:

* **RTP** via the shared ``run_rtp`` harness — the mean payout multiplier over a
  seeded stream, asserted within the harness's variance-aware 5σ CLT half-width of
  ``1 - edge = 0.99`` (NEVER a widened band). For limbo at any target ``t``:
  ``EV = t · (1-edge)/t = 1 - edge``.
* **Tail probability** P(X ≥ target), measured by counting the won-flag directly
  from ``play`` (independent of the multiplier mean), asserted within a 5σ binomial
  CLT half-width of the theoretical ``(1 - edge)/target``.

Stake/limits are irrelevant here — the harness measures the UNCAPPED engine math
(``run_rtp`` applies no cap/rounding), so no maxWin can bind the gate.
"""

from __future__ import annotations

import math
from typing import cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame
from rtp_harness import run_rtp

GAME_ID = "originals.limbo"
TARGET_RTP = 0.99  # 1 - edge (the seeded originals.limbo config carries edge 0.01)

# 5σ → ~6e-7 two-sided false-reject rate; deterministic under the fixed seed below.
# Matches the harness's _Z so RTP and tail gates share one (never widened) width.
_Z = 5.0

# Fixed seed for the tail measurement: the gate is a deterministic function of
# (target, n), so a passing run reproduces exactly — no RNG-driven flakes.
_TAIL_SERVER_SEED = b"limbo-tail-server-seed"
_TAIL_CLIENT_SEED = "limbo-tail"


def _measure_tail(target: float, n: int) -> tuple[float, float, float]:
    """Measure P(X ≥ target) over ``n`` seeded trials, counting the won-flag.

    Returns ``(observed_fraction, expected, half_width)`` where ``expected`` is the
    theoretical ``(1 - edge)/target`` and ``half_width`` is the 5σ binomial CLT
    band ``_Z · sqrt(expected·(1-expected)/n)`` (derived, never hand-set). Counts
    ``detail["won"]`` (X ≥ target) directly, so it validates the curve + threshold
    independently of the payout-multiplier mean the RTP gate measures.
    """
    game = cast("InstantGame", load_game(GAME_ID))
    cfg = default_config(GAME_ID)
    wins = 0
    for nonce in range(n):
        rng = create_rng(_TAIL_SERVER_SEED, _TAIL_CLIENT_SEED, nonce)
        if game.play({"target": target}, rng, cfg).detail["won"]:
            wins += 1
    observed = wins / n
    expected = (1.0 - cfg.edge) / target
    half_width = _Z * math.sqrt(expected * (1.0 - expected) / n)
    return observed, expected, half_width


@pytest.mark.parametrize("target", [1.5, 2.0])
def test_limbo_rtp_fast(target: float) -> None:
    """~1e6 trials on PR: measured RTP within the CLT half-width of 0.99 ({1.5, 2})."""
    result = run_rtp(GAME_ID, {"target": target}, 1_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol


@pytest.mark.parametrize("target", [1.5, 2.0])
def test_limbo_tail_probability_fast(target: float) -> None:
    """~1e6 trials on PR: P(X≥target) within the 5σ binomial band of (1-edge)/target."""
    observed, expected, half_width = _measure_tail(target, 1_000_000)
    assert abs(observed - expected) <= half_width


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("target", [1.5, 2.0, 10.0, 100.0])
def test_limbo_rtp_heavy(target: float) -> None:
    """1e7 trials (CI rtp-gate / nightly): RTP at {1.5,2,10,100} converges to 0.99."""
    result = run_rtp(GAME_ID, {"target": target}, 10_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[limbo RTP] target={target}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("target", [1.5, 2.0, 10.0, 100.0])
def test_limbo_tail_probability_heavy(target: float) -> None:
    """1e7 trials (CI rtp-gate / nightly): P(X≥target) converges to (1-edge)/target."""
    observed, expected, half_width = _measure_tail(target, 10_000_000)
    assert abs(observed - expected) <= half_width
    print(
        f"\n[limbo tail] target={target}: P(X>={target})={observed:.6f} "
        f"expected={expected:.6f} dev={abs(observed - expected):.6f} "
        f"tol(5σ binomial)={half_width:.6f} n=10000000"
    )
