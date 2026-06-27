"""S9 — dice verifier parity: the open verifier reproduces a dice bet bit-for-bit.

The verifier imports ``engine`` verbatim, so ``reproduce(...)`` for
``originals.dice`` recomputes the SAME outcome the bet loop derives — never a
re-implementation (the load-bearing DRY constraint, spec §2.3b). These run the
engine path directly and the verifier path, and assert identity.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce

SERVER_SEED = b"dice-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _engine_outcome(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game("originals.dice"))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config("originals.dice"))


def test_verifier_reproduces_dice_bet_bitforbit() -> None:
    """Verifier outcome == the engine's own play() across nonces and directions."""
    for nonce in range(25):
        for direction in ("UNDER", "OVER"):
            for target in (2, 50, 98):
                server = _engine_outcome(
                    {"target": target, "direction": direction}, nonce
                )
                verified = reproduce(
                    server_seed=SERVER_SEED,
                    client_seed=CLIENT_SEED,
                    nonce=nonce,
                    game_id="originals.dice",
                    input={"target": target, "direction": direction},
                )
                assert verified == server


def test_parity_is_input_sensitive() -> None:
    """The verifier carries ``input`` through: different targets → different outcomes.

    Discriminating — a verifier that dropped ``input`` (passed ``{}``) would
    produce an identical outcome for both and the parity test would be vacuous.
    """
    low = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=1,
        game_id="originals.dice", input={"target": 2, "direction": "UNDER"},
    )
    high = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=1,
        game_id="originals.dice", input={"target": 98, "direction": "UNDER"},
    )
    assert low != high
    assert low.detail["target"] != high.detail["target"]
    # Same nonce → same draw → same roll; only the target (and thus win/mult) differs.
    assert low.detail["roll"] == high.detail["roll"]


def test_parity_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id="originals.dice", input={"target": 50, "direction": "UNDER"},
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id="originals.dice", input={"target": 50, "direction": "UNDER"},
    )
    assert a == b
