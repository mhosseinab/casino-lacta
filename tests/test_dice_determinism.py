"""S9 — dice determinism: same (seed, client, nonce) + input → identical roll.

Pure outcome logic: no IO, no wall-clock, no ``random``. The roll is a pure
function of the seeded stream and the input; the roll equals the spec §A.1 RNG
mapping ``floor(f * 10000) / 100`` of the stream's first draw.
"""

from __future__ import annotations

import math
from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, Outcome

SERVER_SEED = b"dice-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    game = cast("InstantGame", load_game("originals.dice"))
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return game.play(dict(input), rng, default_config("originals.dice"))


def test_same_inputs_same_roll() -> None:
    a = _play({"target": 50, "direction": "UNDER"}, 7)
    b = _play({"target": 50, "direction": "UNDER"}, 7)
    assert a == b  # frozen Outcome dataclass: multiplier + detail
    assert a.detail["roll"] == b.detail["roll"]


def test_distinct_nonces_diverge() -> None:
    """The stream advances with nonce, so rolls vary across a span of bets."""
    rolls = {
        _play({"target": 50, "direction": "UNDER"}, n).detail["roll"] for n in range(20)
    }
    assert len(rolls) > 1


def test_roll_matches_spec_rng_mapping() -> None:
    """roll == floor(f * 10000) / 100 of the stream's first draw (spec §A.1)."""
    for nonce in range(50):
        f = create_rng(SERVER_SEED, CLIENT_SEED, nonce).next()
        expected = math.floor(f * 10_000) / 100
        roll = _play({"target": 50, "direction": "UNDER"}, nonce).detail["roll"]
        assert roll == expected
        assert 0.0 <= roll <= 99.99


def test_over_and_under_share_the_roll_and_split_the_win() -> None:
    """Same seed/nonce → identical roll; UNDER/OVER win on opposite sides of target."""
    target = 50
    for nonce in range(50):
        under = _play({"target": target, "direction": "UNDER"}, nonce)
        over = _play({"target": target, "direction": "OVER"}, nonce)
        roll = under.detail["roll"]
        assert over.detail["roll"] == roll
        assert under.detail["won"] is (roll < target)
        assert over.detail["won"] is (roll > target)
