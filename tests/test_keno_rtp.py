"""S15 — keno RTP-as-CI gate (spec §A.8): three independent measurements.

1. **Analytic table RTP (FAST, every PR — the comprehensive gate).** For EVERY of
   the 30 ``(risk, picks)`` cells, recompute the hypergeometric ``P(h)`` directly
   (``math.comb``) and assert ``Σ_h P(h)·pay_h`` is within ±0.2% of ``1 - edge``
   *after* the 2-decimal rounding baked into the published table. This reads the
   SAME table ``play()`` uses (``default_config(...).params["tables"]``), so it
   guards the real settlement path — not the tuner's self-consistency. It is exact,
   deterministic arithmetic, hence unmarked (runs on every PR).

2. **Hypergeometric hit frequencies (FAST).** Empirically draw via the engine path
   and assert the observed distribution of ``hits`` matches the hypergeometric
   ``P(h)`` within a 5σ binomial band — catches a biased ``sampleWithoutReplacement``.

3. **End-to-end sampling RTP sanity (``run_rtp``).** A few LOW-risk, small-pick cells
   where every paying hit-count is sampled thousands of times, so the harness's
   variance-aware 5σ CLT band is meaningful. Heavy (1e7) cells are marked
   ``rtp_heavy`` for the CI ``rtp-gate`` job. High-variance cells (HIGH / many picks)
   are intentionally excluded from sampling — their top prize is too rare to sample
   (pick-10 all-10 ≈ 1.2e-9); the analytic gate (#1) covers them exactly.

Per the iron rule: the table is OPTIMIZED to hit target; NO tolerance is widened and
NO outcome is clamped.
"""

from __future__ import annotations

import math
from typing import cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame
from rtp_harness import run_rtp

GAME_ID = "originals.keno"
GRID, DRAW = 40, 10
TARGET_RTP = 0.99  # 1 - edge (seeded originals.keno config carries edge 0.01)

# "±0.2%" read as an absolute RTP tolerance (0.002). The tuned tables land far
# tighter (worst cell ≈ 2e-4), so the stricter relative reading (0.00198) also holds.
_ANALYTIC_TOL = 0.002

_RISKS = ("LOW", "MEDIUM", "HIGH")
_Z = 5.0  # 5σ → ~6e-7 two-sided false-reject; deterministic under the fixed seed.

_FREQ_SERVER_SEED = b"keno-freq-server-seed"
_FREQ_CLIENT_SEED = "keno-freq"


def _hypergeom(picks: int, hits: int) -> float:
    """P(hits = h | picks = k): C(k,h)·C(40-k,10-h)/C(40,10) (spec §A.8)."""
    return (
        math.comb(picks, hits)
        * math.comb(GRID - picks, DRAW - hits)
        / math.comb(GRID, DRAW)
    )


# ---- 1. analytic per-(picks, risk) RTP from the published table -------------


def test_analytic_table_rtp_within_tolerance_for_every_cell() -> None:
    """Σ P(h)·pay_h ≈ 1-edge (±0.2%) for all 30 cells, from the live config table."""
    tables = default_config(GAME_ID).params["tables"]
    worst = 0.0
    for risk in _RISKS:
        for picks in range(1, 11):
            pays = tables[risk][str(picks)]
            assert len(pays) == picks + 1  # one entry per hit count 0..picks
            rtp = sum(_hypergeom(picks, h) * pays[h] for h in range(picks + 1))
            dev = abs(rtp - TARGET_RTP)
            worst = max(worst, dev)
            assert dev <= _ANALYTIC_TOL, (
                f"{risk} picks={picks}: RTP {rtp:.6f} deviates {dev:.6f} "
                f"> tol {_ANALYTIC_TOL} from {TARGET_RTP}"
            )
    print(f"\n[keno analytic RTP] worst cell deviation = {worst:.2e} (tol {_ANALYTIC_TOL})")


def test_payouts_are_monotonic_nondecreasing_in_hits() -> None:
    """More hits never pays less — a sanity property of every tuned table."""
    tables = default_config(GAME_ID).params["tables"]
    for risk in _RISKS:
        for picks in range(1, 11):
            pays = tables[risk][str(picks)]
            assert all(pays[h] <= pays[h + 1] for h in range(len(pays) - 1)), (
                f"{risk} picks={picks} not monotonic: {pays}"
            )


# ---- 2. empirical hypergeometric hit-frequency ------------------------------


def _measure_hit_freq(picks: list[int], n: int) -> dict[int, float]:
    game = cast("InstantGame", load_game(GAME_ID))
    cfg = default_config(GAME_ID)
    counts: dict[int, int] = {}
    for nonce in range(n):
        rng = create_rng(_FREQ_SERVER_SEED, _FREQ_CLIENT_SEED, nonce)
        h = game.play({"picks": picks}, rng, cfg).detail["hits"]
        counts[h] = counts.get(h, 0) + 1
    return {h: c / n for h, c in counts.items()}


@pytest.mark.parametrize("k", [3, 8])
def test_hit_frequency_matches_hypergeometric_fast(k: int) -> None:
    """~2e5 trials: observed P(hits=h) within a 5σ binomial band of the hypergeometric."""
    n = 200_000
    picks = list(range(1, k + 1))
    observed = _measure_hit_freq(picks, n)
    for h in range(k + 1):
        expected = _hypergeom(k, h)
        obs = observed.get(h, 0.0)
        half_width = _Z * math.sqrt(max(expected * (1.0 - expected), 1e-12) / n)
        assert abs(obs - expected) <= half_width, (
            f"picks={k} hits={h}: observed {obs:.6f} vs expected {expected:.6f} "
            f"(dev {abs(obs - expected):.6f} > 5σ {half_width:.6f}, n={n})"
        )


@pytest.mark.rtp_heavy
def test_hit_frequency_matches_hypergeometric_heavy() -> None:
    """2e6 trials (CI rtp-gate): the full pick-10 hit distribution converges."""
    n = 2_000_000
    picks = list(range(1, 11))
    observed = _measure_hit_freq(picks, n)
    for h in range(11):
        expected = _hypergeom(10, h)
        obs = observed.get(h, 0.0)
        half_width = _Z * math.sqrt(max(expected * (1.0 - expected), 1e-12) / n)
        assert abs(obs - expected) <= half_width
    print(f"\n[keno hit freq] pick-10 over n={n}: observed={observed}")


# ---- 3. end-to-end sampling RTP sanity (run_rtp harness) --------------------

# LOW risk, small picks: every paying hit-count lands thousands of times at 1e6, so
# the harness's measured variance — and thus its 5σ CLT band — is reliable. Two
# representative cells keep the PR gate quick (each play consumes 10 draws); the
# analytic test (#1) is the comprehensive per-cell gate and broader sampling lives
# in the heavy job.
_FAST_CELLS = [[1, 2], [1, 2, 3, 4]]


@pytest.mark.parametrize("picks", _FAST_CELLS)
def test_keno_sampling_rtp_fast(picks: list[int]) -> None:
    """~1e6 trials on PR: measured RTP within the CLT half-width of 0.99 (LOW risk)."""
    result = run_rtp(GAME_ID, {"picks": picks, "risk": "LOW"}, 1_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol


_HEAVY_CELLS = [
    ("LOW", [1, 2, 3, 4, 5, 6]),
    ("MEDIUM", [1, 2, 3, 4]),
    ("HIGH", [1, 2, 3]),
]


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("risk,picks", _HEAVY_CELLS)
def test_keno_sampling_rtp_heavy(risk: str, picks: list[int]) -> None:
    """1e7 trials (CI rtp-gate): RTP converges to 0.99 for well-sampled cells."""
    result = run_rtp(GAME_ID, {"picks": picks, "risk": risk}, 10_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[keno RTP] {risk} picks={picks}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )
