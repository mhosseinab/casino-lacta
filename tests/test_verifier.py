"""S7 — verifier parity (no DB): the verifier reproduces engine outcomes exactly.

The load-bearing property: the verifier reuses ``engine`` verbatim, so given
``(serverSeed, clientSeed, nonce, input, gameId)`` it reproduces the server's
outcome bit-for-bit — never a re-implementation. These tests run the engine path
directly and the verifier path, and assert identity.
"""

from __future__ import annotations

from typing import cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome
from verifier import reproduce, reproduce_round, verifier_link

SERVER_SEED = b"reveal-on-rotation-server-seed"
CLIENT_SEED = "player-client-seed"


def _engine_outcome(input: dict[str, object], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game("stub.coinflip"))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config("stub.coinflip"))


def test_reproduce_matches_engine_bitforbit() -> None:
    """User/Originals mode == the engine's own play() for the same inputs."""
    for nonce in range(25):
        server = _engine_outcome({"side": "heads"}, nonce)
        verified = reproduce(
            server_seed=SERVER_SEED,
            client_seed=CLIENT_SEED,
            nonce=nonce,
            game_id="stub.coinflip",
            input={"side": "heads"},
        )
        assert verified == server  # frozen Outcome dataclass: multiplier + detail


def test_reproduce_is_input_sensitive() -> None:
    """Carries ``input`` through: betting 'tails' flips the win vs 'heads'.

    Discriminating — a verifier that ignored input (passed ``{}``) could not
    distinguish these and the test would be vacuous.
    """
    # A nonce whose draw lands 'heads' (roll < 0.5): 'heads' wins, 'tails' loses.
    nonce = next(
        n for n in range(100) if create_rng(SERVER_SEED, CLIENT_SEED, n).next() < 0.5
    )
    heads = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
        game_id="stub.coinflip", input={"side": "heads"},
    )
    tails = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=nonce,
        game_id="stub.coinflip", input={"side": "tails"},
    )
    assert heads.detail["won"] is True
    assert tails.detail["won"] is False
    assert heads.multiplier != tails.multiplier


def test_reproduce_is_deterministic() -> None:
    a = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id="stub.coinflip", input={"side": "heads"},
    )
    b = reproduce(
        server_seed=SERVER_SEED, client_seed=CLIENT_SEED, nonce=7,
        game_id="stub.coinflip", input={"side": "heads"},
    )
    assert a == b


def test_round_mode_reproduces_first_float_cursor_zero() -> None:
    """Crash round mode: f = first draw of (roundServerSeed, roundId, roundNumber)."""
    round_server_seed = b"round-server-seed"
    round_id = "round-public-salt"
    round_number = 42
    expected = create_rng(round_server_seed, round_id, round_number).next()
    f = reproduce_round(
        server_seed=round_server_seed, round_id=round_id, round_number=round_number
    )
    assert f == expected
    assert 0.0 <= f < 1.0


def test_round_mode_differs_from_user_mode_labelling() -> None:
    """Same bytes, different seam labels: round uses (roundId, roundNumber)."""
    seed = b"shared-seed"
    # user-mode first float for (clientSeed='x', nonce=3) equals round-mode f for
    # (roundId='x', roundNumber=3) — they ARE the same HMAC stream, proving round
    # mode is a relabelling, not a fork.
    user_first = create_rng(seed, "x", 3).next()
    round_f = reproduce_round(server_seed=seed, round_id="x", round_number=3)
    assert round_f == user_first


def test_verifier_link_hides_unrevealed_seed() -> None:
    """Pre-reveal the link carries the commitment hash, never the seed."""
    pre = verifier_link(
        game_id="stub.coinflip", client_seed="c", nonce=1, server_seed_hash="abc123"
    )
    assert "serverSeedHash=abc123" in pre
    assert "serverSeed=" not in pre.replace("serverSeedHash=abc123", "")

    post = verifier_link(
        game_id="stub.coinflip", client_seed="c", nonce=1,
        server_seed_hash="abc123", server_seed="deadbeef",
    )
    assert "serverSeed=deadbeef" in post
