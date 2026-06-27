"""S9 — dice RTP-as-CI gate: measured RTP ≈ 1 - edge at UNDER targets {2, 50, 98}.

Mirrors the S7 ``tests/test_rtp_stub.py`` split so dice is wired into the
dedicated ``rtp-gate`` CI job (CLAUDE.md DoD: "a game ships only when its RTP/EV
test is wired into that job"):

* ``test_*_fast`` — ~1e6 trials at targets {50, 98}, runs in the default PR
  ``pytest``. Both targets are tight to ±0.5% at 1e6 (5σ CLT bands ~0.0016 /
  ~0.0002), so the gate is meaningful on every PR without a heavy cost. target=2
  is NOT here: at p=0.02 it pays 49.5× (per-trial variance ~48), so its 1e6 band
  (~0.035) is too loose to be a useful PR check — it lives in the heavy gate.
* ``test_*_heavy`` — 1e7 trials at all three targets {2, 50, 98}, marked
  ``rtp_heavy`` and EXCLUDED from the default run (``addopts = -m 'not
  rtp_heavy'``); the dedicated CI ``rtp-gate`` job runs it via ``-m rtp_heavy``.
  This is where target=2 converges to ±0.5%.

The shared harness measures the UNCAPPED engine EV over a seeded stream (one
``create_rng`` per trial → ``Dice.play``) and asserts the mean payout multiplier
is within its variance-aware CLT half-width of the target (``1 - edge = 0.99``);
see ``tests/rtp_harness``. For dice UNDER target ``T`` the win probability is
exactly ``T/100``: the discrete roll maps ``k ∈ {0..9999}`` so ``roll < T`` covers
exactly ``100·T`` of the 10000 equally-likely values, giving
``EV = (T/100)·(0.99/(T/100)) = 0.99`` for every target. Neither test passes a
``tol`` — both exercise the harness's derived 5σ half-width (NEVER a hand-set band;
the iron rule: never widen/contrive a tolerance to pass).
"""

from __future__ import annotations

import pytest

from rtp_harness import run_rtp

GAME_ID = "originals.dice"
TARGET_RTP = 0.99  # 1 - edge (the seeded originals.dice config carries edge 0.01)


@pytest.mark.parametrize("target", [50, 98])
def test_dice_under_rtp_fast(target: int) -> None:
    """~1e6 trials on PR: measured RTP within the CLT half-width of 0.99 ({50,98})."""
    result = run_rtp(GAME_ID, {"target": target, "direction": "UNDER"}, 1_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("target", [2, 50, 98])
def test_dice_under_rtp_heavy(target: int) -> None:
    """1e7 trials (CI rtp-gate / nightly): {2,50,98} converge to ±0.5% of 0.99."""
    result = run_rtp(GAME_ID, {"target": target, "direction": "UNDER"}, 10_000_000, TARGET_RTP)
    # run_rtp already asserts |rtp - target| <= its variance-aware CLT half-width
    # internally; re-assert against the SAME (never widened) tolerance for clarity.
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[dice RTP] UNDER target={target}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )
