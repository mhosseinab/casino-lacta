"""S12 — Mines EV exactness + the reference multiplier table (pure, no IO/clock/random).

The load-bearing math gate (spec §A.6). For every (M, k):

    fairMultiplier(k) = C(25, k) / C(25 - M, k)
    payout(k)         = (1 - edge) · fairMultiplier(k)        # the engine multiplier
    P(survive k)      = C(25 - M, k) / C(25, k) = 1 / fairMultiplier(k)
    EV(cash after k)  = P(survive k) · payout(k) = 1 - edge   # EXACT, any k, any M

The EV here is exact PRE-rounding (it operates on the float multiplier the engine
returns, not floored money), so the assertion is a hard equality to ``1 - edge``.
A mistuned multiplier (e.g. dropping the ``1 - edge`` factor, or an off-by-one in
``C(25 - M, k)``) breaks it.
"""

from __future__ import annotations

import math
from typing import cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, StatefulGame

GAME_ID = "originals.mines"
EDGE = 0.01
TARGET_EV = 1.0 - EDGE  # 0.99
SERVER_SEED = b"mines-ev-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> StatefulGame:
    return cast("StatefulGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


def _fair_multiplier(mines: int, k: int) -> float:
    return math.comb(25, k) / math.comb(25 - mines, k)


def _cashout_multiplier(mines: int, k: int, nonce: int) -> float:
    """Drive a REAL round: init the layout, reveal k safe cells, cash out, read the
    engine's settlement multiplier. White-box: the test reads ``mine_positions`` from
    the opaque state to choose provably-safe cells (the app never does this)."""
    game = _game()
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    state = game.init({"mines": mines}, rng, _cfg())
    safe = [c for c in range(25) if c not in set(state["mine_positions"])]
    assert len(safe) >= k
    for cell in safe[:k]:
        state, _ = game.step(state, {"op": "reveal", "cell": cell})
    state, outcome = game.step(state, {"op": "cashout"})
    assert outcome is not None
    assert outcome.detail["status"] == "CASHED_OUT"
    return outcome.multiplier


@pytest.mark.parametrize("mines", [1, 3, 5])
@pytest.mark.parametrize("k", [1, 2, 3, 4, 5])
def test_ev_is_exactly_one_minus_edge(mines: int, k: int) -> None:
    """EV(cash after k) == 1 - edge EXACTLY, for M in {1,3,5}, k in {1..5}."""
    payout = _cashout_multiplier(mines, k, nonce=mines * 10 + k)
    fair = _fair_multiplier(mines, k)
    survive = 1.0 / fair
    ev = survive * payout
    assert ev == pytest.approx(TARGET_EV, abs=1e-12), (
        f"M={mines} k={k}: payout={payout} fair={fair} EV={ev} != {TARGET_EV}"
    )


@pytest.mark.parametrize("mines", [1, 3, 5])
@pytest.mark.parametrize("k", [1, 2, 3, 4, 5])
def test_payout_multiplier_equals_edge_adjusted_fair(mines: int, k: int) -> None:
    """The engine settlement multiplier == (1 - edge) · C(25,k)/C(25-M,k)."""
    payout = _cashout_multiplier(mines, k, nonce=mines + k)
    expected = TARGET_EV * _fair_multiplier(mines, k)
    assert payout == pytest.approx(expected, rel=1e-12)


# The spec §A.6 reference table (edge = 0.01), rounded to 3 dp.
_REFERENCE = {
    (1, 1): 1.031, (3, 1): 1.125, (5, 1): 1.238,
    (1, 2): 1.076, (3, 2): 1.286, (5, 2): 1.563,
    (1, 3): 1.125, (3, 3): 1.479, (5, 3): 1.997,
    (1, 4): 1.179, (3, 4): 1.712, (5, 4): 2.585,
    (1, 5): 1.238, (3, 5): 1.997, (5, 5): 3.393,
}


@pytest.mark.parametrize(("mines", "k"), sorted(_REFERENCE))
def test_matches_spec_reference_multipliers(mines: int, k: int) -> None:
    """Engine payout multipliers round to the published spec §A.6 values."""
    payout = _cashout_multiplier(mines, k, nonce=99)
    assert round(payout, 3) == _REFERENCE[(mines, k)]
