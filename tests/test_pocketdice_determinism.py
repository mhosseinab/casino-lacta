"""S11 — pocket dice determinism + input fence: pure outcome logic, no IO/clock/random.

The outcome is a pure function of the seeded stream: two draws map to
``die = floor(f*6)+1`` each, summed to ``s ∈ [2, 12]`` (the 2d6 triangular sum,
spec §A.2). ``UNDER`` wins on ``s < target``, ``OVER`` on ``s > target``; a win
pays ``(1-edge)/p`` where ``p`` is the true probability of the winning region.
The ``validate_input`` fence rejects structurally invalid targets deterministically.
"""

from __future__ import annotations

import math
from typing import Any, cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InstantGame, InvalidBetInput, Outcome

GAME_ID = "originals.pocketdice"
SERVER_SEED = b"pocketdice-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> InstantGame:
    return cast("InstantGame", load_game(GAME_ID))


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, default_config(GAME_ID))


def test_same_inputs_same_outcome() -> None:
    a = _play({"target": 7, "direction": "OVER"}, 7)
    b = _play({"target": 7, "direction": "OVER"}, 7)
    assert a == b  # frozen Outcome dataclass: multiplier + detail
    assert a.detail["sum"] == b.detail["sum"]


def test_consumes_exactly_two_draws() -> None:
    """play advances the stream by exactly two draws (two dice, spec §A.2)."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    _game().play({"target": 7, "direction": "OVER"}, rng, default_config(GAME_ID))
    assert rng.cursor == 2


def test_distinct_nonces_diverge() -> None:
    """The stream advances with nonce, so the sum varies across a span of bets."""
    sums = {_play({"target": 7, "direction": "OVER"}, n).detail["sum"] for n in range(50)}
    assert len(sums) > 1


def test_sum_matches_two_die_mapping() -> None:
    """sum == (floor(f1*6)+1) + (floor(f2*6)+1) of the stream's first two draws.

    Asserts pocket dice maps each draw to a uniform 1..6 die and sums them, rather
    than inlining some other formula — the parity-bearing derivation.
    """
    for nonce in range(50):
        rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
        d1 = math.floor(rng.next() * 6) + 1
        d2 = math.floor(rng.next() * 6) + 1
        outcome = _play({"target": 7, "direction": "OVER"}, nonce)
        assert outcome.detail["dice"] == [d1, d2]
        assert outcome.detail["sum"] == d1 + d2
        assert 2 <= outcome.detail["sum"] <= 12


def test_dice_are_in_range() -> None:
    """Each die lands in 1..6 across many nonces (floor(f*6)+1, f ∈ [0,1))."""
    for nonce in range(200):
        d1, d2 = _play({"target": 7, "direction": "OVER"}, nonce).detail["dice"]
        assert 1 <= d1 <= 6
        assert 1 <= d2 <= 6


@pytest.mark.parametrize("direction", ["OVER", "UNDER"])
def test_win_logic_and_payout(direction: str) -> None:
    """OVER wins on sum>target, UNDER on sum<target; a win pays (1-edge)/p, loss 0."""
    target = 7
    edge = default_config(GAME_ID).edge
    for nonce in range(60):
        outcome = _play({"target": target, "direction": direction}, nonce)
        s = outcome.detail["sum"]
        won = s > target if direction == "OVER" else s < target
        assert outcome.detail["won"] is won
        if won:
            # p for target 7, either direction, is 15/36; multiplier = 0.99 / (15/36).
            assert outcome.multiplier == pytest.approx((1.0 - edge) / (15 / 36))
        else:
            assert outcome.multiplier == 0.0


def test_direction_defaults_to_under() -> None:
    """An omitted direction defaults to UNDER (mirrors dice)."""
    assert _play({"target": 7}, 4) == _play({"target": 7, "direction": "UNDER"}, 4)


# --- input fence -----------------------------------------------------------

_CFG: GameConfig = default_config(GAME_ID)


@pytest.mark.parametrize("bad", [{}, {"direction": "OVER"}])
def test_validate_input_rejects_missing_target(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _CFG)


@pytest.mark.parametrize("bad", ["abc", None, [1]])
def test_validate_input_rejects_non_numeric_target(bad: Any) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": bad}, _CFG)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_validate_input_rejects_non_finite_target(bad: float) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": bad}, _CFG)


@pytest.mark.parametrize("bad", [7.5, 2.5, 11.99])
def test_validate_input_rejects_non_integer_target(bad: float) -> None:
    """Sums are integers — a fractional target is not part of the 2d6 model."""
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": bad, "direction": "OVER"}, _CFG)


def test_validate_input_rejects_invalid_direction() -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": 7, "direction": "SIDEWAYS"}, _CFG)


@pytest.mark.parametrize(
    "bad",
    [
        {"target": 12, "direction": "OVER"},  # p = 0 (no sum > 12)
        {"target": 2, "direction": "UNDER"},  # p = 0 (no sum < 2)
        {"target": 1, "direction": "OVER"},  # below achievable band
        {"target": 13, "direction": "UNDER"},  # above achievable band
        {"target": 0, "direction": "OVER"},
        {"target": 20, "direction": "UNDER"},
    ],
)
def test_validate_input_rejects_zero_prob_or_out_of_band(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _CFG)


@pytest.mark.parametrize(
    "ok",
    [
        {"target": 2, "direction": "OVER"},  # p = 35/36 (band edge)
        {"target": 11, "direction": "OVER"},  # p = 1/36 (band edge)
        {"target": 12, "direction": "UNDER"},  # p = 35/36 (band edge)
        {"target": 3, "direction": "UNDER"},  # p = 1/36 (band edge)
        {"target": 7, "direction": "OVER"},
        {"target": 7, "direction": "UNDER"},
        {"target": 7},  # direction defaults to UNDER
        {"target": 7, "direction": "over"},  # case-insensitive
        {"target": 7.0, "direction": "OVER"},  # integer-valued float
    ],
)
def test_validate_input_accepts_valid(ok: dict[str, Any]) -> None:
    assert _game().validate_input(ok, _CFG) is None
