"""S15 — keno verifier parity: the open verifier reproduces a keno bet bit-for-bit.

The verifier imports ``engine`` verbatim, so ``reproduce(...)`` for ``originals.keno``
recomputes the SAME draw + hits + multiplier the bet loop derives — never a
re-implementation (spec §2.3b).
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce

GAME_ID = "originals.keno"
SERVER_SEED = b"keno-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _engine_outcome(input: dict[str, Any], nonce: int) -> Outcome:
    game = cast("InstantGame", load_game(GAME_ID))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config(GAME_ID))


def test_verifier_reproduces_keno_bet_bitforbit() -> None:
    """Verifier outcome == the engine's own play() across nonces, picks, and risks."""
    inputs = [
        {"picks": [4], "risk": "LOW"},
        {"picks": [3, 7, 11], "risk": "MEDIUM"},
        {"picks": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "risk": "HIGH"},
    ]
    for nonce in range(25):
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
    """Same draw, different risk → same drawn/hits but the multiplier differs.

    Discriminating: a verifier that dropped ``input`` would settle both identically.
    We find a nonce whose hit count pays differently across risk tables.
    """
    picks = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    for nonce in range(100):
        low = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"picks": picks, "risk": "LOW"},
        )
        high = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"picks": picks, "risk": "HIGH"},
        )
        assert low.detail["drawn"] == high.detail["drawn"]  # same draw
        assert low.detail["hits"] == high.detail["hits"]
        if low.multiplier != high.multiplier:
            assert low != high
            return
    raise AssertionError("no nonce produced diverging multipliers — test is vacuous")


def test_parity_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"picks": [3, 7, 11], "risk": "MEDIUM"},
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"picks": [3, 7, 11], "risk": "MEDIUM"},
    )
    assert a == b
