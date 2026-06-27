"""S9.5 — the per-game input fence at the engine level (pure, no DB).

``Dice.validate_input`` is the consumer-shaped seam the bet loop calls at the
boundary BEFORE any money moves: it rejects targets/directions that push the win
probability ``p`` outside the spec §A.1 band ``[0.0001, 0.98]`` (and malformed
input), raising the pure, deterministic ``engine.types.InvalidBetInput``; it
returns ``None`` for valid input. Being pure, it is unit-testable with no DB,
no RNG, and no framework.
"""

from __future__ import annotations

import pytest

from engine.games.dice import GAME as DICE
from engine.types import GameConfig, InvalidBetInput

_CFG = GameConfig(edge=0.01, params={})


@pytest.mark.parametrize(
    "input",
    [
        {"target": 50, "direction": "UNDER"},  # p = 0.50
        {"target": 50, "direction": "OVER"},  # p = 0.50
        {"target": 50},  # direction defaults to UNDER
        {"target": 0.01, "direction": "UNDER"},  # p = 0.0001 (lower bound, inclusive)
        {"target": 98, "direction": "UNDER"},  # p = 0.98 (upper bound, inclusive)
        {"target": 99.99, "direction": "OVER"},  # p = 0.0001 (OVER lower bound)
        {"target": 2, "direction": "OVER"},  # p = 0.98 (OVER upper bound)
        {"target": 50, "direction": "under"},  # case-insensitive direction
    ],
)
def test_validate_input_accepts_in_band(input: dict[str, object]) -> None:
    assert DICE.validate_input(input, _CFG) is None


@pytest.mark.parametrize(
    "input",
    [
        {"target": 98.01, "direction": "UNDER"},  # p = 0.9801 > 0.98
        {"target": 0, "direction": "UNDER"},  # p = 0 < 0.0001
        {"target": 100, "direction": "UNDER"},  # p = 1.0 > 0.98
        {"target": 1.99, "direction": "OVER"},  # p = 0.9801 > 0.98
        {"target": 100, "direction": "OVER"},  # p = 0 < 0.0001
        {"target": 50, "direction": "SIDEWAYS"},  # invalid direction
        {"direction": "UNDER"},  # missing target
        {"target": "abc", "direction": "UNDER"},  # non-numeric target
    ],
)
def test_validate_input_rejects_out_of_band_or_malformed(input: dict[str, object]) -> None:
    with pytest.raises(InvalidBetInput):
        DICE.validate_input(input, _CFG)
