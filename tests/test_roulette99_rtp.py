"""S16 — roulette99 RTP-as-CI gate + result uniformity (spec §A.9).

Mirrors the limbo gate split (``tests/test_limbo_rtp.py``) so roulette is wired
into the dedicated ``rtp-gate`` CI job (CLAUDE.md DoD):

* ``test_*_fast`` — ~1e6 trials for the low-variance colours RED (2.02×) and BLACK
  (1.98×): per-trial variance is small so the 5σ CLT band is tight on every PR.
  Plus a result-uniformity check (each of 0–99 within a 5σ binomial band).
* ``test_*_heavy`` — 1e7 trials including GREEN (99×, variance ~98, like limbo's
  100× target): marked ``rtp_heavy`` and EXCLUDED from the default run; the CI
  ``rtp-gate`` job runs it via ``-m rtp_heavy``.

Per-bet RTP is measured by placing a SINGLE colour bet so the aggregate
multiplier equals that bet's own multiplier — ``EV = p · (1-edge)/p = 1-edge``
for every colour. Tolerance is the harness's variance-aware 5σ CLT half-width,
never a widened band. The harness measures UNCAPPED engine math (no cap/round).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from engine.registry import default_config
from engine.rng import create_rng
from rtp_harness import run_rtp

GAME_ID = "originals.roulette"
TARGET_RTP = 0.99  # 1 - edge (the seeded originals.roulette config carries edge 0.01)

_Z = 5.0  # 5σ → ~6e-7 two-sided false-reject rate; deterministic under the fixed seed.

_UNIFORM_SERVER_SEED = b"roulette-uniform-server-seed"
_UNIFORM_CLIENT_SEED = "roulette-uniform"


def _single(colour: str, stake: int = 100) -> dict[str, Any]:
    return {"bets": [{"value": colour, "stakeMinor": stake}]}


def _measure_uniformity(n: int) -> tuple[list[int], float, float]:
    """Count result occurrences over ``n`` seeded spins.

    Returns ``(counts, expected, half_width)`` where ``expected = n/100`` and
    ``half_width`` is the 5σ binomial band ``_Z·sqrt(n·p·(1-p))`` with ``p=1/100``
    (derived, never hand-set)."""
    counts = [0] * 100
    for nonce in range(n):
        f = create_rng(_UNIFORM_SERVER_SEED, _UNIFORM_CLIENT_SEED, nonce).next()
        counts[math.floor(f * 100)] += 1
    p = 1.0 / 100.0
    expected = n * p
    half_width = _Z * math.sqrt(n * p * (1.0 - p))
    return counts, expected, half_width


# --------------------------------------------------------------------------- fast


@pytest.mark.parametrize("colour", ["RED", "BLACK"])
def test_roulette_rtp_fast(colour: str) -> None:
    """~1e6 trials on PR: per-bet RTP within the CLT half-width of 0.99 (RED/BLACK)."""
    result = run_rtp(GAME_ID, _single(colour), 1_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol


def test_result_uniform_over_0_99_fast() -> None:
    """~5e5 spins on PR: every result 0–99 within the 5σ binomial band of n/100."""
    n = 500_000
    counts, expected, half_width = _measure_uniformity(n)
    assert sum(counts) == n
    for value, count in enumerate(counts):
        assert abs(count - expected) <= half_width, (
            f"result {value}: count {count} deviates from {expected:.1f} "
            f"by {abs(count - expected):.1f} > 5σ band {half_width:.1f}"
        )


# --------------------------------------------------------------------------- heavy


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("colour", ["GREEN", "RED", "BLACK"])
def test_roulette_rtp_heavy(colour: str) -> None:
    """1e7 trials (CI rtp-gate / nightly): per-bet RTP converges to 0.99 incl. GREEN (99×)."""
    cfg = default_config(GAME_ID)
    p = len(cfg.params["pockets"][colour]) / 100.0
    result = run_rtp(GAME_ID, _single(colour), 10_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[roulette RTP] colour={colour} (p={p}): rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )


@pytest.mark.rtp_heavy
def test_result_uniform_over_0_99_heavy() -> None:
    """1e7 spins (CI rtp-gate / nightly): result distribution converges to uniform."""
    n = 10_000_000
    counts, expected, half_width = _measure_uniformity(n)
    assert sum(counts) == n
    worst = max(abs(c - expected) for c in counts)
    for value, count in enumerate(counts):
        assert abs(count - expected) <= half_width, (
            f"result {value}: count {count} deviates from {expected:.1f} "
            f"by {abs(count - expected):.1f} > 5σ band {half_width:.1f}"
        )
    print(
        f"\n[roulette uniformity] n={n}: expected={expected:.1f}/bucket "
        f"worst_dev={worst:.1f} tol(5σ binomial)={half_width:.1f}"
    )
