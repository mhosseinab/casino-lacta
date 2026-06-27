"""S16 — roulette99 verifier parity: the open verifier reproduces a spin bit-for-bit.

The verifier imports ``engine`` verbatim, so ``reproduce(...)`` for
``originals.roulette`` recomputes the SAME outcome the bet loop derives — never a
re-implementation (the load-bearing DRY constraint, spec §2.3b). These run the
engine path directly and the verifier path and assert identity, including the
per-bet settlements carried through from ``input``.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce

GAME_ID = "originals.roulette"
SERVER_SEED = b"roulette-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _engine_outcome(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game(GAME_ID))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config(GAME_ID))


def _multi() -> dict[str, Any]:
    return {
        "bets": [
            {"value": "RED", "stakeMinor": 100},
            {"value": "BLACK", "stakeMinor": 250},
            {"value": "GREEN", "stakeMinor": 50},
        ]
    }


def test_verifier_reproduces_roulette_spin_bitforbit() -> None:
    """Verifier outcome == the engine's own play() across nonces and bet sets."""
    inputs = [
        {"bets": [{"value": "RED", "stakeMinor": 100}]},
        {"bets": [{"value": "BLACK", "stakeMinor": 100}]},
        {"bets": [{"value": "GREEN", "stakeMinor": 100}]},
        _multi(),
    ]
    for nonce in range(40):
        for input in inputs:
            server = _engine_outcome(input, nonce)
            verified = reproduce(
                server_seed=SERVER_SEED,
                client_seed=CLIENT_SEED,
                nonce=nonce,
                game_id=GAME_ID,
                input=input,
            )
            assert verified == server


def test_parity_is_input_sensitive() -> None:
    """The verifier carries ``input`` through: same spin (same draw → same result),
    different colour bets → the result is identical but the win flags differ.

    Discriminating — a verifier that dropped ``input`` would settle both bets
    identically and the parity test would be vacuous."""
    for nonce in range(200):
        red = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"bets": [{"value": "RED", "stakeMinor": 100}]},
        )
        black = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"bets": [{"value": "BLACK", "stakeMinor": 100}]},
        )
        # Same nonce → same draw → same result; only the placed colour differs.
        assert red.detail["result"] == black.detail["result"]
        red_won = red.detail["settlements"][0]["won"]
        black_won = black.detail["settlements"][0]["won"]
        if red_won != black_won:
            assert red != black
            return
    raise AssertionError("no nonce produced diverging win flags — test is vacuous")


def test_parity_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input=_multi(),
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input=_multi(),
    )
    assert a == b
