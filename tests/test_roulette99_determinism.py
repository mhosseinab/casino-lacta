"""S16 — roulette99 determinism + input fence: pure outcome logic, no IO/clock/random.

The spin result ``floor(f*100)`` ∈ {0,…,99} is a pure function of the seeded
stream's first draw; the colour is read from the configured ``pockets`` map (never
a hard literal). Each placed colour bet settles at ``(1-edge)/p`` on a match else
``0``; the bet loop sees a single stake-weighted aggregate ``multiplier`` (the
only aggregation that makes ``floor(total_stake · multiplier)`` the total payout).
The ``validate_input`` fence rejects malformed / out-of-range ``bets``
deterministically.
"""

from __future__ import annotations

import math
from typing import Any, cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InstantGame, InvalidBetInput, Outcome

GAME_ID = "originals.roulette"
SERVER_SEED = b"roulette-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> InstantGame:
    return cast("InstantGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, _cfg())


def _expected_result(nonce: int) -> int:
    """The independent reference: floor(first-draw · 100) ∈ {0,…,99}."""
    f = create_rng(SERVER_SEED, CLIENT_SEED, nonce).next()
    return math.floor(f * 100)


def _colour_of(result: int) -> str:
    """Reference colour lookup over the configured default pocket map."""
    pockets = _cfg().params["pockets"]
    for colour, results in pockets.items():
        if result in results:
            return str(colour)
    raise AssertionError(f"result {result} not covered by pockets map")


def _find_nonce_with_colour(colour: str, span: int = 5000) -> int:
    for nonce in range(span):
        if _colour_of(_expected_result(nonce)) == colour:
            return nonce
    raise AssertionError(f"no nonce in 0..{span} produced colour {colour}")


def _single(colour: str, stake: int = 100) -> dict[str, Any]:
    return {"bets": [{"type": "COLOUR", "value": colour, "stakeMinor": stake}]}


# --------------------------------------------------------------------------- determinism


def test_same_inputs_same_outcome() -> None:
    a = _play(_single("RED"), 7)
    b = _play(_single("RED"), 7)
    assert a == b  # frozen Outcome: multiplier + detail
    assert a.detail["result"] == b.detail["result"]


def test_result_matches_floor_of_first_draw() -> None:
    """result == floor(f·100) ∈ {0,…,99} for the stream's first draw (spec §A.9)."""
    for nonce in range(200):
        outcome = _play(_single("RED"), nonce)
        result = outcome.detail["result"]
        assert result == _expected_result(nonce)
        assert 0 <= result <= 99


def test_colour_read_from_configured_map() -> None:
    """The reported colour is the configured pocket for the result (not a literal)."""
    for nonce in range(300):
        outcome = _play(_single("RED"), nonce)
        assert outcome.detail["colour"] == _colour_of(outcome.detail["result"])


def test_consumes_exactly_one_draw() -> None:
    """One spin draws exactly one randomFloat (spec §A.9 RNG mapping)."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    _game().play(_single("RED"), rng, _cfg())
    assert rng.cursor == 1


def test_distinct_nonces_diverge() -> None:
    results = {_play(_single("RED"), n).detail["result"] for n in range(50)}
    assert len(results) > 1


# --------------------------------------------------------------------------- settlement


@pytest.mark.parametrize("colour", ["GREEN", "RED", "BLACK"])
def test_winning_colour_pays_one_minus_edge_over_p(colour: str) -> None:
    """A single matching bet pays exactly ``(1-edge)/p``; a miss pays 0."""
    cfg = _cfg()
    pockets = cfg.params["pockets"]
    p = len(pockets[colour]) / 100.0
    win_mult = (1.0 - cfg.edge) / p
    nonce = _find_nonce_with_colour(colour)
    outcome = _play(_single(colour), nonce)
    settlement = outcome.detail["settlements"][0]
    assert settlement["won"] is True
    assert settlement["value"] == colour
    assert settlement["multiplier"] == pytest.approx(win_mult)
    # single bet → aggregate multiplier == the per-bet multiplier
    assert outcome.multiplier == pytest.approx(win_mult)


def test_losing_colour_pays_zero() -> None:
    red_nonce = _find_nonce_with_colour("RED")
    outcome = _play(_single("BLACK"), red_nonce)
    settlement = outcome.detail["settlements"][0]
    assert settlement["won"] is False
    assert settlement["multiplier"] == 0.0
    assert outcome.multiplier == 0.0


def test_multiple_simultaneous_bets_settle_each_and_aggregate() -> None:
    """Multiple colour bets on one spin: each settles independently and the
    aggregate multiplier is stake-weighted so ``floor(total_stake · multiplier)``
    equals the total payout the loop credits."""
    cfg = _cfg()
    pockets = cfg.params["pockets"]
    red_nonce = _find_nonce_with_colour("RED")
    bets = [
        {"value": "RED", "stakeMinor": 100},
        {"value": "BLACK", "stakeMinor": 250},
        {"value": "GREEN", "stakeMinor": 50},
    ]
    outcome = _play({"bets": bets}, red_nonce)
    settlements = outcome.detail["settlements"]
    assert [s["won"] for s in settlements] == [True, False, False]

    total_stake = sum(b["stakeMinor"] for b in bets)
    p_red = len(pockets["RED"]) / 100.0
    red_payout = 100 * (1.0 - cfg.edge) / p_red
    expected_total = red_payout  # only RED won
    # the only correct aggregation: floor(total_stake · multiplier) == total payout
    assert math.floor(total_stake * outcome.multiplier) == math.floor(expected_total)
    assert outcome.multiplier == pytest.approx(expected_total / total_stake)


def test_same_colour_placed_twice_both_win() -> None:
    red_nonce = _find_nonce_with_colour("RED")
    bets = [
        {"value": "RED", "stakeMinor": 100},
        {"value": "RED", "stakeMinor": 100},
    ]
    outcome = _play({"bets": bets}, red_nonce)
    assert all(s["won"] for s in outcome.detail["settlements"])


# --------------------------------------------------------------------------- input fence


@pytest.mark.parametrize("bad", [{}, {"bets": []}, {"bets": "RED"}])
def test_validate_input_rejects_missing_or_empty_bets(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _cfg())


@pytest.mark.parametrize(
    "bet",
    [
        {"stakeMinor": 100},  # no value
        {"value": "PURPLE", "stakeMinor": 100},  # unknown colour
        {"value": "RED"},  # no stake
        {"value": "RED", "stakeMinor": 0},  # non-positive
        {"value": "RED", "stakeMinor": -5},
        {"value": "RED", "stakeMinor": 1.5},  # non-integer
        {"value": "RED", "stakeMinor": "100"},  # non-integer
    ],
)
def test_validate_input_rejects_malformed_bet(bet: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": [bet]}, _cfg())


def test_validate_input_rejects_when_any_bet_malformed() -> None:
    bets = [{"value": "RED", "stakeMinor": 100}, {"value": "PURPLE", "stakeMinor": 100}]
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": bets}, _cfg())


def test_validate_input_accepts_valid_bets() -> None:
    bets = [
        {"type": "COLOUR", "value": "RED", "stakeMinor": 100},
        {"value": "BLACK", "stakeMinor": 50},
        {"value": "GREEN", "stakeMinor": 1},
    ]
    assert _game().validate_input({"bets": bets}, _cfg()) is None
