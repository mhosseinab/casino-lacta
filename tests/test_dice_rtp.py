"""S9 — dice RTP-as-CI gate: measured RTP ≈ 1 - edge at UNDER targets {2, 50, 98}.

The shared harness measures the UNCAPPED engine EV over a seeded stream (one
``create_rng`` per trial → ``Dice.play``) and asserts the mean payout multiplier
is within its variance-aware CLT half-width of the target (``1 - edge = 0.99``);
see ``tests/rtp_harness``. For dice UNDER target ``T`` the win probability is
exactly ``T/100``: the discrete roll maps ``k ∈ {0..9999}`` so ``roll < T`` covers
exactly ``100·T`` of the 10000 equally-likely values, giving
``EV = (T/100)·(0.99/(T/100)) = 0.99`` for every target. Verified empirically here
over 1e7 rolls per target.

These run UNMARKED at 1e7 each so the fixed Verify command executes them directly
(the repo deselects ``rtp_heavy`` via ``addopts = -m 'not rtp_heavy'`` — marking
them would silently deselect the gate and prove nothing). target=2 pays 49.5× at
``p=0.02``, so its per-trial variance (~48) makes the 5-sigma half-width ~1.1% at
1e7 — the harness gate is variance-aware, so we assert ONLY ``|rtp - target| <=
result.tol`` and never a hand-set flat band (the iron rule: never widen/contrive a
tolerance to pass). The measured deviation is well within ±0.5% at this n and is
printed for the Verify report.
"""

from __future__ import annotations

import pytest

from rtp_harness import run_rtp

GAME_ID = "originals.dice"
TARGET_RTP = 0.99  # 1 - edge (the seeded originals.dice config carries edge 0.01)
N = 10_000_000


@pytest.mark.parametrize("target", [2, 50, 98])
def test_dice_under_rtp(target: int) -> None:
    """UNDER {2,50,98}: measured RTP within the harness's variance-aware window of 0.99."""
    result = run_rtp(GAME_ID, {"target": target, "direction": "UNDER"}, N, TARGET_RTP)
    # run_rtp already asserts |rtp - target| <= its CLT half-width internally; we
    # re-assert against the SAME (never widened) tolerance for clarity.
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    deviation = abs(result.rtp - TARGET_RTP)
    print(
        f"\n[dice RTP] UNDER target={target}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={deviation:.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n={N}"
    )
