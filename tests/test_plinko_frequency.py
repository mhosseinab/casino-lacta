"""S13 — plinko bin-frequency gate: landing bins follow Binomial(R, ½).

Independent of the RTP/payout math: counts the LANDING BIN over many seeded trials
and asserts each bin's observed frequency is within a derived 5σ binomial CLT
half-width of the theoretical ``P(i) = C(R,i)/2^R`` (mirrors the limbo tail gate's
``_Z·sqrt(p(1-p)/n)`` — NEVER a flat band). This validates the RNG mapping
``rightBounces = Σ[f < 0.5]`` produces a fair (p=½) board.

A representative board runs on every PR; all supported row counts converge in the
heavy ``rtp_heavy`` gate wired into the CI ``rtp-gate`` job.
"""

from __future__ import annotations

import math
from math import comb
from typing import cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame

GAME_ID = "originals.plinko"

# 5σ → ~6e-7 two-sided false-reject rate; deterministic under the fixed seed below.
_Z = 5.0
_FREQ_SERVER_SEED = b"plinko-frequency-server-seed"
_FREQ_CLIENT_SEED = "plinko-frequency"

_SUPPORTED_ROWS = sorted(int(r) for r in default_config(GAME_ID).params["tables"])


def _binomial_probs(rows: int) -> list[float]:
    denom = 2**rows
    return [comb(rows, i) / denom for i in range(rows + 1)]


def _measure_bins(rows: int, n: int) -> list[int]:
    """Count landing-bin occurrences over ``n`` seeded trials (risk is irrelevant
    to the bin distribution — the payout table never feeds back into the bounce)."""
    game = cast("InstantGame", load_game(GAME_ID))
    cfg = default_config(GAME_ID)
    counts = [0] * (rows + 1)
    for nonce in range(n):
        rng = create_rng(_FREQ_SERVER_SEED, _FREQ_CLIENT_SEED, nonce)
        bin_index = game.play({"rows": rows, "risk": "MEDIUM"}, rng, cfg).detail["bin"]
        counts[bin_index] += 1
    return counts


def _assert_binomial(rows: int, counts: list[int], n: int) -> None:
    probs = _binomial_probs(rows)
    for i, (count, p) in enumerate(zip(counts, probs, strict=True)):
        observed = count / n
        half_width = _Z * math.sqrt(p * (1.0 - p) / n)
        assert abs(observed - p) <= half_width, (
            f"plinko rows={rows} bin={i}: freq {observed:.6e} deviates from "
            f"P(i)={p:.6e} by {abs(observed - p):.6e} > 5σ {half_width:.6e} (n={n})"
        )


def test_plinko_bin_frequencies_fast() -> None:
    """~1e6 trials on PR: R=8 bin frequencies match Binomial(8, ½) within 5σ."""
    n = 1_000_000
    _assert_binomial(8, _measure_bins(8, n), n)


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("rows", _SUPPORTED_ROWS)
def test_plinko_bin_frequencies_heavy(rows: int) -> None:
    """1e7 trials (CI rtp-gate): every board's bins match Binomial(R, ½) within 5σ."""
    n = 10_000_000
    counts = _measure_bins(rows, n)
    _assert_binomial(rows, counts, n)
    print(f"\n[plinko freq] rows={rows}: counts={counts} n={n}")
