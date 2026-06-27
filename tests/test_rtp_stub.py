"""S7 — the RTP-as-CI gate, proven on ``stub.coinflip`` (target 0.99).

The gate measures the UNCAPPED engine EV: a fair 50/50 paying 1.98x has
EV = 0.5 * 1.98 = 0.99 (a 1% house edge). The tolerance is the harness's CLT
half-width ``z * sqrt(Var/n)`` (NOT a flat ±0.5%): for coinflip
Var = 0.5*1.98^2 - 0.99^2 ≈ 0.9801, so at n=1e6 the 5-sigma window is ≈ ±0.005.

* ``test_*_fast`` — ~1e6 trials, runs in the normal ``pytest`` (PR) suite.
* ``test_*_heavy`` — 1e7 trials, marked ``rtp_heavy`` and EXCLUDED from the
  default run (``addopts = -m 'not rtp_heavy'``); the dedicated CI ``rtp-gate``
  job runs it via ``-m rtp_heavy``.

Neither test passes a ``tol`` — both exercise the harness's derived half-width.
"""

from __future__ import annotations

import pytest

from rtp_harness import run_rtp

GAME_ID = "stub.coinflip"
TARGET_RTP = 0.99
# Cosmetic side; a fair coin makes the chosen side irrelevant to the edge.
INPUT = {"side": "heads"}


def test_stub_coinflip_rtp_fast() -> None:
    """~1e6 trials on PR: measured RTP within the CLT half-width of 0.99."""
    result = run_rtp(GAME_ID, INPUT, 1_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    # The gate must be a TIGHT CLT window, not a loose flat band — guards against
    # a future regression that silently widens the tolerance.
    assert result.tol < 0.01


@pytest.mark.rtp_heavy
def test_stub_coinflip_rtp_heavy() -> None:
    """1e7 trials (CI rtp-gate / nightly): the gate narrows as n grows."""
    result = run_rtp(GAME_ID, INPUT, 10_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    assert result.tol < 0.005
