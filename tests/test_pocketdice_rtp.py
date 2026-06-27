"""S11 — pocket dice RTP-as-CI gate: empirical 2d6 pmf + per-target RTP ≈ 1 - edge.

Pocket dice bets on the sum of 2d6 (triangular distribution, spec §A.2). Two
independent gates, neither with a hand-set tolerance:

* **Empirical pmf** — the observed frequency of each sum 2..12 over a seeded
  stream is within a 5σ binomial CLT half-width of the theoretical ``count/36``
  (1,2,3,4,5,6,5,4,3,2,1 out of 36). This validates the die mapping
  ``floor(f*6)+1`` and the triangular sum independently of any payout.
* **RTP** via the shared ``run_rtp`` harness — the mean payout multiplier over a
  seeded stream, asserted within the harness's variance-aware 5σ CLT half-width of
  ``1 - edge = 0.99``. For any target/direction with win probability ``p``:
  ``EV = p · (1-edge)/p = 1 - edge``, because ``p`` is the TRUE probability of the
  winning region (computed from the pmf counts, never a hardcoded paytable).

Variance per bet is ``0.9801·(36/count − 1)``: the boundary targets (OVER 11 /
UNDER 3 at p=1/36 → var≈34; OVER 9 / UNDER 5 at p=6/36 → var≈4.9) are too loose to
gate at 1e6, so they live in the heavy gate; the fast PR gate uses mid targets
(var ≤ ~1.4). Neither test passes a ``tol`` — both exercise the harness's derived
5σ half-width (NEVER a hand-set/widened band; the iron rule).
"""

from __future__ import annotations

import math
from typing import cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame
from rtp_harness import run_rtp

GAME_ID = "originals.pocketdice"
TARGET_RTP = 0.99  # 1 - edge (the seeded originals.pocketdice config carries edge 0.01)

# 5σ → ~6e-7 two-sided false-reject rate; deterministic under the fixed seed below.
_Z = 5.0

# Theoretical 2d6 pmf: counts out of 36 for each sum (spec §A.2).
_PMF_COUNTS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
_OUTCOMES = 36

_PMF_SERVER_SEED = b"pocketdice-pmf-server-seed"
_PMF_CLIENT_SEED = "pocketdice-pmf"


def test_empirical_pmf_matches_2d6() -> None:
    """Each sum's observed frequency is within a 5σ binomial band of count/36."""
    n = 1_000_000
    game = cast("InstantGame", load_game(GAME_ID))
    cfg = default_config(GAME_ID)
    counts = dict.fromkeys(_PMF_COUNTS, 0)
    for nonce in range(n):
        rng = create_rng(_PMF_SERVER_SEED, _PMF_CLIENT_SEED, nonce)
        s = game.play({"target": 7, "direction": "OVER"}, rng, cfg).detail["sum"]
        counts[s] += 1

    assert sum(counts.values()) == n  # every sum landed in 2..12
    for s, count in _PMF_COUNTS.items():
        observed = counts[s] / n
        expected = count / _OUTCOMES
        half_width = _Z * math.sqrt(expected * (1.0 - expected) / n)
        assert abs(observed - expected) <= half_width, (
            f"sum {s}: observed {observed:.6f} vs expected {expected:.6f} "
            f"dev {abs(observed - expected):.6f} > 5σ band {half_width:.6f}"
        )


# Fast PR gate: mid targets (var ≤ ~1.4), both directions.
_FAST = [(4, "OVER"), (7, "OVER"), (10, "UNDER"), (7, "UNDER")]
# Heavy gate: the full spec §A.2 table, both directions (incl. high-variance edges).
_HEAVY = [
    (2, "OVER"), (4, "OVER"), (7, "OVER"), (9, "OVER"), (11, "OVER"),
    (12, "UNDER"), (10, "UNDER"), (7, "UNDER"), (5, "UNDER"), (3, "UNDER"),
]


@pytest.mark.parametrize(("target", "direction"), _FAST)
def test_pocketdice_rtp_fast(target: int, direction: str) -> None:
    """~1e6 trials on PR: measured RTP within the CLT half-width of 0.99 (mid targets)."""
    result = run_rtp(
        GAME_ID, {"target": target, "direction": direction}, 1_000_000, TARGET_RTP
    )
    assert abs(result.rtp - TARGET_RTP) <= result.tol


@pytest.mark.rtp_heavy
@pytest.mark.parametrize(("target", "direction"), _HEAVY)
def test_pocketdice_rtp_heavy(target: int, direction: str) -> None:
    """1e7 trials (CI rtp-gate / nightly): every spec target/direction converges to 0.99."""
    result = run_rtp(
        GAME_ID, {"target": target, "direction": direction}, 10_000_000, TARGET_RTP
    )
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[pocketdice RTP] {direction} target={target}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )
