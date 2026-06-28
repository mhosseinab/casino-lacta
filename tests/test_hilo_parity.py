"""S14 — HiLo verifier parity: the verifier replays the FULL step sequence.

HiLo draws a fresh card every guess, so a parity test that reproduced only the first
card (init) would be vacuous on the part that matters — the per-step draws. The open
verifier imports ``engine`` verbatim and drives ONE seeded stream forward through the
SAME actions (``reproduce_stateful_sequence``).

The discriminating claim is the saga's resumable cursor: the app reconstructs the
stream at the persisted cursor before EACH step (``HmacRngStream(seed, client, nonce,
cursor)``). This test reproduces that step-by-step reconstruction as the "server" and
asserts it equals the verifier's continuous-stream replay bit-for-bit.
"""

from __future__ import annotations

from typing import Any, cast

from engine.registry import default_config, load_game
from engine.rng import HmacRngStream
from engine.types import StatefulGame
from verifier import reproduce_stateful_sequence

GAME_ID = "originals.hilo"
SERVER_SEED = b"hilo-parity-server-seed"
CLIENT_SEED = "player-client-seed"


def _server_play(
    nonce: int, sides: list[str]
) -> tuple[list[int], list[float | None], list[dict[str, Any]]]:
    """Drive the round the way the SAGA does: reconstruct the stream at the stored
    cursor before each step (resumable from (seed, client, nonce, cursor)). Returns the
    revealed cards, the cumulative multipliers, AND the actions actually executed (the
    server never sends an action after the round ends)."""
    game = cast("StatefulGame", load_game(GAME_ID))
    cfg = default_config(GAME_ID)
    init_rng = HmacRngStream(SERVER_SEED, CLIENT_SEED, nonce, cursor=0)
    state = game.init({}, init_rng, cfg)
    cursor = init_rng.cursor  # 1 after the first shown card
    revealed: list[int] = []
    mults: list[float | None] = []
    executed: list[dict[str, Any]] = []
    for side in sides:
        if state["status"] != "ACTIVE":
            break
        action = {"op": "guess", "side": side}
        step_rng = HmacRngStream(SERVER_SEED, CLIENT_SEED, nonce, cursor=cursor)
        state, outcome = game.step(state, action, step_rng)
        cursor = step_rng.cursor
        assert outcome is not None
        executed.append(action)
        revealed.append(int(outcome.detail["revealedRank"]))
        mults.append(outcome.detail.get("currentMultiplier"))
    return revealed, mults, executed


def _verifier_play(
    nonce: int, actions: list[dict[str, Any]], server_seed: bytes = SERVER_SEED
) -> tuple[list[int], list[float | None]]:
    _state, outcomes = reproduce_stateful_sequence(
        server_seed=server_seed,
        client_seed=CLIENT_SEED,
        nonce=nonce,
        game_id=GAME_ID,
        actions=actions,
    )
    revealed = [int(o.detail["revealedRank"]) for o in outcomes if o is not None]
    mults = [o.detail.get("currentMultiplier") for o in outcomes if o is not None]
    return revealed, mults


def test_server_equals_verifier_full_sequence() -> None:
    """The saga's per-step cursor reconstruction == the verifier's continuous stream,
    across the whole revealed-card / cumulative-multiplier sequence."""
    sides = ["HIGHER", "LOWER", "HIGHER", "LOWER", "HIGHER", "LOWER", "HIGHER", "LOWER"]
    for nonce in range(40):
        s_rev, s_mult, executed = _server_play(nonce, sides)
        v_rev, v_mult = _verifier_play(nonce, executed)
        assert s_rev == v_rev, f"nonce {nonce}: revealed cards diverge"
        assert s_mult == v_mult, f"nonce {nonce}: multipliers diverge"


def test_parity_sequence_is_non_vacuous() -> None:
    """The replayed sequence must actually exercise multiple distinct draws across
    steps (otherwise 'parity' would be trivially satisfied by a constant)."""
    sides = ["HIGHER"] * 12
    all_revealed: list[int] = []
    multi_step_rounds = 0
    for nonce in range(40):
        s_rev, _, _ = _server_play(nonce, sides)
        all_revealed.extend(s_rev)
        if len(s_rev) > 1:
            multi_step_rounds += 1
    assert len(set(all_revealed)) > 1, "no diversity in drawn cards — test is vacuous"
    assert multi_step_rounds > 0, "no round advanced past the first card — parity vacuous"


def test_parity_is_seed_sensitive() -> None:
    """A different server seed yields a different sequence for the same actions —
    a verifier that ignored the seed would return the same cards. Compares the FIRST
    card of each round across nonces (one guess never steps past a terminal)."""
    one_guess = [{"op": "guess", "side": "HIGHER"}]
    for nonce in range(40):
        base, _ = _verifier_play(nonce, one_guess, server_seed=SERVER_SEED)
        other, _ = _verifier_play(nonce, one_guess, server_seed=b"a-different-server-seed")
        if base and other and base != other:
            return
    raise AssertionError("no nonce produced a diverging first card — test is vacuous")
