"""S10 — limbo verifier parity: the open verifier reproduces a limbo bet bit-for-bit.

The verifier imports ``engine`` verbatim, so ``reproduce(...)`` for
``originals.limbo`` recomputes the SAME outcome the bet loop derives — never a
re-implementation (the load-bearing DRY constraint, spec §2.3b). These run the
engine path directly and the verifier path, and assert identity.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce

GAME_ID = "originals.limbo"
SERVER_SEED = b"limbo-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _engine_outcome(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game(GAME_ID))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config(GAME_ID))


def test_verifier_reproduces_limbo_bet_bitforbit() -> None:
    """Verifier outcome == the engine's own play() across nonces and targets."""
    for nonce in range(25):
        for target in (1.5, 2.0, 10.0, 100.0):
            server = _engine_outcome({"target": target}, nonce)
            verified = reproduce(
                server_seed=SERVER_SEED,
                client_seed=CLIENT_SEED,
                nonce=nonce,
                game_id=GAME_ID,
                input={"target": target},
            )
            assert verified == server


def test_parity_is_input_sensitive() -> None:
    """The verifier carries ``input`` through: same draw, different targets → the
    generated X is identical but the win/multiplier differ.

    Discriminating — a verifier that dropped ``input`` would settle both bets
    identically and the parity test would be vacuous. We pick a nonce whose
    generated X lands between the two targets so the win flags actually diverge.
    """
    low_target, high_target = 1.5, 1_000_000.0
    for nonce in range(100):
        low = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"target": low_target},
        )
        high = reproduce(
            server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
            game_id=GAME_ID, input={"target": high_target},
        )
        # Same nonce → same draw → same generated X; only target/win/mult differ.
        assert low.detail["generated"] == high.detail["generated"]
        if low.detail["won"] != high.detail["won"]:
            assert low != high
            assert low.detail["target"] != high.detail["target"]
            return
    raise AssertionError("no nonce produced diverging win flags — test is vacuous")


def test_parity_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"target": 2.0},
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id=GAME_ID, input={"target": 2.0},
    )
    assert a == b
