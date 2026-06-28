"""S12 — Mines verifier parity: the open verifier reproduces the committed layout.

A stateful game's provable-fairness claim is that the hidden mine layout is FIXED at
round start by ``(server_seed, client_seed, nonce, input)``. The verifier imports
``engine`` verbatim, so ``reproduce_stateful_init`` recomputes the SAME layout the
bet loop committed — never a re-implementation (spec §2.3b). Post-reveal this lets a
player confirm the mines were not moved after their clicks.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import StatefulGame
from verifier import reproduce_stateful_init

GAME_ID = "originals.mines"
SERVER_SEED = b"mines-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _server_layout(input: dict[str, Any], nonce: int) -> list[int]:
    game = cast("StatefulGame", load_game(GAME_ID))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.init(dict(input), rng, default_config(GAME_ID))["mine_positions"]


def test_verifier_reproduces_layout_bitforbit() -> None:
    """Verifier layout == the engine's own init() across nonces and mine counts."""
    for mines in (1, 3, 5, 12, 24):
        for nonce in range(25):
            server = _server_layout({"mines": mines}, nonce)
            verified = reproduce_stateful_init(
                server_seed=SERVER_SEED,
                client_seed=CLIENT_SEED,
                nonce=nonce,
                game_id=GAME_ID,
                input={"mines": mines},
            )["mine_positions"]
            assert verified == server


def test_parity_is_input_sensitive() -> None:
    """A different mine count yields a different committed layout for the same stream.

    Discriminating: a verifier that ignored ``input`` would return the same layout.
    """
    for nonce in range(50):
        three = reproduce_stateful_init(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"mines": 3},
        )["mine_positions"]
        five = reproduce_stateful_init(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"mines": 5},
        )["mine_positions"]
        if three != five:
            return
    raise AssertionError("no nonce produced diverging layouts — test is vacuous")


def test_parity_is_deterministic() -> None:
    a = reproduce_stateful_init(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"mines": 5},
    )
    b = reproduce_stateful_init(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"mines": 5},
    )
    assert a["mine_positions"] == b["mine_positions"]
