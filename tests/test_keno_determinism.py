"""S15 — keno determinism, draw shape, and the input fence (pure, no IO/clock/random).

The drawn set + hits + multiplier are a pure function of the seeded stream and the
tuned table; ``play`` consumes exactly 10 draws regardless of ``|picks|``. The
``validate_input`` fence rejects structurally invalid selections deterministically.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InstantGame, InvalidBetInput, Outcome

GAME_ID = "originals.keno"
SERVER_SEED = b"keno-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> InstantGame:
    return cast("InstantGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, _cfg())


def test_same_inputs_same_outcome() -> None:
    a = _play({"picks": [3, 7, 11], "risk": "MEDIUM"}, 7)
    b = _play({"picks": [3, 7, 11], "risk": "MEDIUM"}, 7)
    assert a == b
    assert a.detail["drawn"] == b.detail["drawn"]


def test_draw_is_ten_distinct_in_grid() -> None:
    for nonce in range(50):
        drawn = _play({"picks": [1, 2, 3]}, nonce).detail["drawn"]
        assert len(drawn) == 10
        assert len(set(drawn)) == 10
        assert all(1 <= x <= 40 for x in drawn)


def test_hits_match_intersection() -> None:
    picks = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    for nonce in range(50):
        out = _play({"picks": picks}, nonce)
        assert out.detail["hits"] == len(set(picks) & set(out.detail["drawn"]))


def test_consumes_exactly_ten_draws_regardless_of_picks() -> None:
    """play advances the stream by exactly 10 draws (the 40→10 sample), not |picks|."""
    for picks in ([5], [1, 2, 3], list(range(1, 11))):
        rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
        _game().play({"picks": picks}, rng, _cfg())
        assert rng.cursor == 10


def test_multiplier_comes_from_the_tuned_table() -> None:
    """multiplier == params table[risk][picks][hits] — the published value, not derived."""
    cfg = _cfg()
    tables = cfg.params["tables"]
    for risk in ("LOW", "MEDIUM", "HIGH"):
        picks = [2, 4, 6, 8, 10]
        for nonce in range(30):
            out = _play({"picks": picks, "risk": risk}, nonce)
            expected = tables[risk][str(len(picks))][out.detail["hits"]]
            assert out.multiplier == float(expected)


def test_risk_defaults_to_medium() -> None:
    picks = [1, 2, 3, 4]
    for nonce in range(20):
        assert _play({"picks": picks}, nonce) == _play({"picks": picks, "risk": "MEDIUM"}, nonce)


def test_risk_is_case_insensitive() -> None:
    picks = [1, 2, 3, 4]
    assert _play({"picks": picks, "risk": "low"}, 9) == _play({"picks": picks, "risk": "LOW"}, 9)


def test_distinct_nonces_diverge() -> None:
    drawn = {tuple(_play({"picks": [1, 2, 3]}, n).detail["drawn"]) for n in range(20)}
    assert len(drawn) > 1


# --- the input fence (validate_input) ---------------------------------------


@pytest.mark.parametrize(
    "ok",
    [
        {"picks": [5]},  # min picks, default risk
        {"picks": list(range(1, 11))},  # max picks
        {"picks": [1, 40], "risk": "LOW"},  # grid endpoints
        {"picks": [10, 20, 30], "risk": "MEDIUM"},
        {"picks": [7], "risk": "HIGH"},
        {"picks": [7], "risk": "high"},  # case-insensitive
        {"picks": (2, 4, 6)},  # tuple accepted
    ],
)
def test_validate_input_accepts_valid(ok: dict[str, Any]) -> None:
    assert _game().validate_input(ok, _cfg()) is None


@pytest.mark.parametrize(
    "bad",
    [
        {},  # missing picks
        {"picks": []},  # too few
        {"picks": list(range(1, 12))},  # 11 > 10
        {"picks": [1, 1, 2]},  # not distinct
        {"picks": [0, 1, 2]},  # 0 out of range
        {"picks": [1, 2, 41]},  # 41 out of range
        {"picks": [1, 2, -3]},  # negative
        {"picks": [1, 2, 3.0]},  # non-integer
        {"picks": [1, 2, "3"]},  # non-integer
        {"picks": [True, 2, 3]},  # bool is not a valid pick
        {"picks": 5},  # not a list
        {"picks": "123"},  # str is not a list of picks
        {"picks": [1, 2, 3], "risk": "EXTREME"},  # invalid risk
    ],
)
def test_validate_input_rejects_invalid(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _cfg())
