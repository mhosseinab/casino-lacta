"""S13 — plinko verifier parity: the open verifier reproduces a plinko bet bit-for-bit.

The verifier imports ``engine`` verbatim, so ``reproduce(...)`` for
``originals.plinko`` recomputes the SAME outcome (bin, path, multiplier) the bet
loop derives — never a re-implementation (the load-bearing DRY constraint, spec
§2.3b). The published per-(rows,risk) tables live in the registry ``GameConfig.params``
so ``default_config`` feeds the verifier the identical table the server settled on.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce

GAME_ID = "originals.plinko"
SERVER_SEED = b"plinko-parity-server-seed"
CLIENT_SEED = "player-client-seed"

_COMBOS = [(8, "LOW"), (12, "MEDIUM"), (16, "HIGH")]


def _engine_outcome(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game(GAME_ID))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config(GAME_ID))


def test_verifier_reproduces_plinko_bet_bitforbit() -> None:
    """Verifier outcome == the engine's own play() across nonces and (rows,risk)."""
    for nonce in range(25):
        for rows, risk in _COMBOS:
            server = _engine_outcome({"rows": rows, "risk": risk}, nonce)
            verified = reproduce(
                server_seed=SERVER_SEED,
                client_seed=CLIENT_SEED,
                nonce=nonce,
                game_id=GAME_ID,
                input={"rows": rows, "risk": risk},
            )
            assert verified == server


def test_parity_is_input_sensitive() -> None:
    """The verifier carries ``input`` through: same draws (same rows) but a
    different risk selects a different multiplier table — the bin/path are
    identical, the multiplier differs. A verifier that dropped ``input`` would
    settle both identically and the parity test would be vacuous."""
    rows = 16
    for nonce in range(100):
        low = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"rows": rows, "risk": "LOW"},
        )
        high = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"rows": rows, "risk": "HIGH"},
        )
        assert low.detail["bin"] == high.detail["bin"]
        assert low.detail["path"] == high.detail["path"]
        if low.multiplier != high.multiplier:
            assert low != high
            return
    raise AssertionError("no nonce produced diverging multipliers — test is vacuous")


def test_parity_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"rows": 16, "risk": "HIGH"},
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"rows": 16, "risk": "HIGH"},
    )
    assert a == b
