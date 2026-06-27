"""S13 — plinko RTP gate: analytic table RTP AND a sampling sanity check.

Two independent, non-substitutable measurements (spec §A.5 "Tests"):

* **Analytic** (deterministic arithmetic, no sampling) — the EXACT
  ``Σ_i P(i)·m_i`` computed from the PUBLISHED 2-dp table with ``P(i) =
  C(R,i)/2^R``, asserted within the spec's ``±0.2%`` (0.002) of ``1 - edge`` for
  EVERY ``(rows, risk)``. This is the tuning gate — it proves the published
  numbers were optimised to hit target AFTER rounding (never a clamp/loosen).
* **Sampling** via the shared ``run_rtp`` harness (limbo layout) — the mean
  payout multiplier over a seeded stream, asserted within the harness's
  variance-aware 5σ CLT half-width of ``1 - edge`` (NEVER a hand-set band). Low-
  variance combos run on every PR; the high-variance HIGH-risk combos converge in
  the heavy ``rtp_heavy`` gate wired into the CI ``rtp-gate`` job.
"""

from __future__ import annotations

from math import comb

import pytest

from engine.registry import default_config
from rtp_harness import run_rtp

GAME_ID = "originals.plinko"

_cfg = default_config(GAME_ID)
TARGET_RTP = 1.0 - _cfg.edge  # 1 - edge (seeded originals.plinko config carries edge 0.01)

# Spec §A.5: published-table RTP within ±0.2% of target after rounding.
_ANALYTIC_TOL = 0.002

# Every supported (rows, risk) combo, derived from the published config (no literals).
_COMBOS = [
    (int(rows_s), risk)
    for rows_s, by_risk in _cfg.params["tables"].items()
    for risk in by_risk
]


def _binomial_probs(rows: int) -> list[float]:
    """P(bin=i) = C(R,i)/2^R for a symmetric (p=½) Plinko board."""
    denom = 2**rows
    return [comb(rows, i) / denom for i in range(rows + 1)]


@pytest.mark.parametrize("rows,risk", _COMBOS)
def test_published_table_analytic_rtp(rows: int, risk: str) -> None:
    """EXACT Σ P(i)·m_i from the published table within ±0.2% for every (rows,risk)."""
    table = _cfg.params["tables"][str(rows)][risk]
    assert len(table) == rows + 1
    probs = _binomial_probs(rows)
    analytic_rtp = sum(p * m for p, m in zip(probs, table, strict=True))
    assert abs(analytic_rtp - TARGET_RTP) <= _ANALYTIC_TOL, (
        f"plinko (rows={rows}, risk={risk}) analytic RTP {analytic_rtp:.6f} "
        f"deviates from {TARGET_RTP:.6f} by {abs(analytic_rtp - TARGET_RTP):.6f} "
        f"> {_ANALYTIC_TOL} — re-tune the published table (never clamp/loosen)"
    )


@pytest.mark.parametrize("rows,risk", _COMBOS)
def test_published_table_is_symmetric_and_nonneg(rows: int, risk: str) -> None:
    """A Plinko board is mirror-symmetric (bin i == bin R-i) and pays >= 0."""
    table = _cfg.params["tables"][str(rows)][risk]
    assert all(m >= 0 for m in table)
    assert all(table[i] == table[rows - i] for i in range(rows + 1))


# Sampling sanity — low-variance combos on PR, all combos in the heavy gate.
_FAST_COMBOS = [(8, "LOW"), (16, "LOW")]


@pytest.mark.parametrize("rows,risk", _FAST_COMBOS)
def test_plinko_rtp_fast(rows: int, risk: str) -> None:
    """~1e6 trials on PR: measured RTP within the CLT half-width of 1-edge (LOW risk)."""
    result = run_rtp(GAME_ID, {"rows": rows, "risk": risk}, 1_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("rows,risk", _COMBOS)
def test_plinko_rtp_heavy(rows: int, risk: str) -> None:
    """1e7 trials (CI rtp-gate): RTP converges to 1-edge for every (rows,risk)."""
    result = run_rtp(GAME_ID, {"rows": rows, "risk": risk}, 10_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[plinko RTP] rows={rows} risk={risk}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )
