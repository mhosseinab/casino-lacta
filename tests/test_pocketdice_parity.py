"""S11 — pocket dice verifier parity: the open verifier reproduces a bet bit-for-bit.

The verifier imports ``engine`` verbatim, so ``reproduce(...)`` for
``originals.pocketdice`` recomputes the SAME outcome the bet loop derives — never a
re-implementation (the load-bearing DRY constraint, spec §2.3b). These run the
engine path directly and the verifier path and assert identity.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce

GAME_ID = "originals.pocketdice"
SERVER_SEED = b"pocketdice-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _engine_outcome(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game(GAME_ID))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config(GAME_ID))


def test_verifier_reproduces_pocketdice_bet_bitforbit() -> None:
    """Verifier outcome == the engine's own play() across nonces, targets, directions."""
    for nonce in range(25):
        for target, direction in ((4, "OVER"), (7, "UNDER"), (11, "OVER"), (3, "UNDER")):
            server = _engine_outcome({"target": target, "direction": direction}, nonce)
            verified = reproduce(
                server_seed=SERVER_SEED,
                client_seed=CLIENT_SEED,
                nonce=nonce,
                game_id=GAME_ID,
                input={"target": target, "direction": direction},
            )
            assert verified == server


def test_parity_is_input_sensitive() -> None:
    """The verifier carries ``input`` through: same draw (same nonce → same dice),
    opposite directions on the same target give the same sum but diverging win flags.

    Discriminating — a verifier that dropped ``input`` would settle both bets
    identically and the parity test would be vacuous. We pick a nonce whose sum is
    not exactly the target so OVER and UNDER actually disagree.
    """
    target = 7
    for nonce in range(100):
        over = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"target": target, "direction": "OVER"},
        )
        under = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"target": target, "direction": "UNDER"},
        )
        # Same nonce → same draws → same sum; only direction/win/mult differ.
        assert over.detail["sum"] == under.detail["sum"]
        if over.detail["won"] != under.detail["won"]:
            assert over != under
            assert over.detail["direction"] != under.detail["direction"]
            return
    raise AssertionError("no nonce produced diverging win flags — test is vacuous")


def test_parity_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"target": 7, "direction": "OVER"},
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"target": 7, "direction": "OVER"},
    )
    assert a == b
