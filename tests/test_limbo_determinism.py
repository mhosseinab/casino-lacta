"""S10 — limbo determinism + input fence: pure outcome logic, no IO/clock/random.

The generated multiplier ``X`` is a pure function of the seeded stream and equals
the shared §2.4 curve ``crash_point(f, edge)`` applied to the stream's first draw
(``engine.games._curve`` — the carry-forward reused by Crash S18). The
``validate_input`` fence rejects structurally invalid targets deterministically.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from engine.games._curve import crash_point
from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, InvalidBetInput, Outcome

GAME_ID = "originals.limbo"
SERVER_SEED = b"limbo-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> InstantGame:
    return cast("InstantGame", load_game(GAME_ID))


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, default_config(GAME_ID))


def test_same_inputs_same_generated() -> None:
    a = _play({"target": 2.0}, 7)
    b = _play({"target": 2.0}, 7)
    assert a == b  # frozen Outcome dataclass: multiplier + detail
    assert a.detail["generated"] == b.detail["generated"]


def test_distinct_nonces_diverge() -> None:
    """The stream advances with nonce, so generated X varies across a span of bets."""
    generated = {_play({"target": 2.0}, n).detail["generated"] for n in range(20)}
    assert len(generated) > 1


def test_generated_matches_shared_curve() -> None:
    """generated == crash_point(f, edge) of the stream's first draw (spec §2.4).

    Asserts limbo reuses the shared carry-forward helper rather than inlining the
    formula — the same ``f`` that Crash (S18) will draw maps to the same ``X``.
    """
    edge = default_config(GAME_ID).edge
    for nonce in range(50):
        f = create_rng(SERVER_SEED, CLIENT_SEED, nonce).next()
        expected = crash_point(f, edge)
        generated = _play({"target": 2.0}, nonce).detail["generated"]
        assert generated == expected
        assert generated >= 1.00


def test_win_pays_target_loss_pays_zero() -> None:
    """Win iff generated >= target; a win pays exactly ``target``, a loss pays 0."""
    target = 2.0
    for nonce in range(50):
        outcome = _play({"target": target}, nonce)
        won = outcome.detail["generated"] >= target
        assert outcome.detail["won"] is won
        assert outcome.multiplier == (target if won else 0.0)


def test_target_multiplier_alias_is_equivalent() -> None:
    """The API-envelope alias ``targetMultiplier`` resolves identically to ``target``."""
    assert _play({"target": 3.0}, 11) == _play({"targetMultiplier": 3.0}, 11)


def test_consumes_exactly_one_draw() -> None:
    """play advances the stream by exactly one draw (one randomFloat, spec §A.3)."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    _game().play({"target": 2.0}, rng, default_config(GAME_ID))
    assert rng.cursor == 1


@pytest.mark.parametrize("bad", [{}, {"direction": "OVER"}])
def test_validate_input_rejects_missing_target(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, default_config(GAME_ID))


@pytest.mark.parametrize("bad", ["abc", None, [1]])
def test_validate_input_rejects_non_numeric_target(bad: Any) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": bad}, default_config(GAME_ID))


@pytest.mark.parametrize("bad", [1.0, 0.99, 0.0, -5.0])
def test_validate_input_rejects_target_below_minimum(bad: float) -> None:
    """target <= 1.00 is a guaranteed win (X is floored at 1.00×), not a gamble."""
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": bad}, default_config(GAME_ID))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_validate_input_rejects_non_finite_target(bad: float) -> None:
    """NaN/inf slip past a bare ``< min`` comparison, so the fence rejects them explicitly."""
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"target": bad}, default_config(GAME_ID))


@pytest.mark.parametrize("ok", [1.01, 1.5, 2.0, 10.0, 100.0, 1000000.0])
def test_validate_input_accepts_valid_targets(ok: float) -> None:
    """A valid target passes the fence (returns None) — incl. the alias form."""
    cfg = default_config(GAME_ID)
    assert _game().validate_input({"target": ok}, cfg) is None
    assert _game().validate_input({"targetMultiplier": ok}, cfg) is None
