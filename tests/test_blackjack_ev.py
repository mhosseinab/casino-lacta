"""S25 — blackjack basic-strategy EV gate: measured edge ≈ the published house edge.

The load-bearing math gate. A basic-strategy player is simulated over ``n`` seeded
rounds (reusing ``engine`` verbatim) and the mean NET result per round is asserted to
match the published house edge for the EXACT default rule set:

    DEFAULT RULE SET (engine.table.blackjack.DEFAULT_PARAMS)
      6 decks · dealer STANDS on soft 17 (S17) · blackjack pays 3:2 ·
      double on any two cards · double after split (DAS) · split to 4 hands ·
      split aces get one card each (no resplit of aces) · NO surrender ·
      dealer peeks for blackjack · insurance offered on an ace up-card.

    Published house edge for this rule set ≈ 0.40%  (e.g. Wizard of Odds:
    6-deck, S17, DAS, no surrender). So  E[net] ≈ −0.0040  per unit of the
    INITIAL bet — doubles/splits/insurance inflate the TOTAL wagered, so the edge
    is defined per initial bet = −E[net], NOT gross-return / base.

Tolerance is the CLT half-width ``z·sqrt(Var/n)`` (z = 5 σ), NOT a flat band — the
same idiom as ``tests/rtp_harness``. It NARROWS as ``n`` grows; the iron rule "never
widen a tolerance to pass" is structural (you cannot loosen it without lowering ``z``
or ``n`` — a visible change). If the measured EV misses, the bug is in the RULES /
strategy, never the tolerance.

A small fast variant (unmarked) gates every PR; the heavy variant is ``rtp_heavy``
(excluded by the repo's ``-m 'not rtp_heavy'`` addopts; the CI rtp-gate opts in).
"""

from __future__ import annotations

import math

import pytest

from blackjack_sim import GAME_ID, net_of, play_round
from engine.registry import default_config

# Published house edge for the default rule set (see module docstring). The EV TARGET
# is set from the published source FIRST; the sim must land inside the CLT window.
PUBLISHED_HOUSE_EDGE = 0.0040
TARGET_NET = -PUBLISHED_HOUSE_EDGE

SERVER_SEED = b"blackjack-ev-server-seed"
CLIENT_SEED = "blackjack-ev"
_Z = 5.0  # 5-sigma gate (matches rtp_harness) → P(|Z|>5) ≈ 6e-7; deterministic anyway.


def _measure(n: int) -> tuple[float, float]:
    """Mean and population variance of the per-round NET result over ``n`` seeded rounds."""
    cfg = default_config(GAME_ID)
    total = 0.0
    total_sq = 0.0
    for nonce in range(n):
        net = net_of(play_round(cfg, SERVER_SEED, CLIENT_SEED, nonce))
        total += net
        total_sq += net * net
    mean = total / n
    variance = max(total_sq / n - mean * mean, 0.0)
    return mean, variance


def _assert_ev(mean: float, variance: float, n: int) -> None:
    half_width = _Z * math.sqrt(variance / n)
    assert abs(mean - TARGET_NET) <= half_width, (
        f"blackjack basic-strategy EV {mean:.6f} deviates from target {TARGET_NET:.6f} "
        f"by {abs(mean - TARGET_NET):.6f} > {_Z}-sigma tol {half_width:.6f} "
        f"(n={n}, var={variance:.4f}) — fix the rules/strategy, never the tolerance"
    )
    # Coarse sanity independent of the CLT gate: a near-fair game with a small house
    # edge and realistic blackjack variance (catches gross errors even if n were cut).
    assert -0.03 < mean < 0.01, f"net EV {mean} outside the plausible blackjack band"
    assert 0.8 < variance < 2.5, f"variance {variance} implausible for blackjack net"


def test_basic_strategy_ev_fast() -> None:
    """Fast PR gate (~1.2e5 rounds): catches gross rule shifts (BJ-payout ~2.3%,
    dealer-draw rule ~2%) with wide margin while staying inside the 5-sigma window."""
    n = 120_000
    mean, variance = _measure(n)
    _assert_ev(mean, variance, n)


@pytest.mark.rtp_heavy
def test_basic_strategy_ev_heavy() -> None:
    """Heavy rtp-gate sim (2e6 rounds): half-width ≈ 0.4%, pinning the measured edge
    tightly to the published ~0.40% house edge."""
    n = 2_000_000
    mean, variance = _measure(n)
    _assert_ev(mean, variance, n)
    assert mean < 0.0  # at this n the player edge is reliably negative
