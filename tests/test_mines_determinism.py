"""S12 — Mines layout determinism, the state machine, and the input/action fences.

The mine layout is committed at ``init`` from the seeded stream (spec §A.6): the
SAME ``(server_seed, client_seed, nonce, input)`` always yields the SAME positions
(outcome fixed at round start, not at click time). ``step`` is a pure transition;
``validate_input`` rejects malformed bet input deterministically.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InvalidBetInput, StatefulGame

GAME_ID = "originals.mines"
SERVER_SEED = b"mines-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> StatefulGame:
    return cast("StatefulGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


def _init(input: dict[str, Any], nonce: int) -> dict[str, Any]:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().init(dict(input), rng, _cfg())


# Mines IGNORES the per-step rng (its layout is committed at init), so any stream
# instance is fine here — the protocol requires the parameter, the game discards it.
def _step(
    state: dict[str, Any], action: dict[str, Any]
) -> tuple[dict[str, Any], Any]:
    return _game().step(state, action, create_rng(SERVER_SEED, CLIENT_SEED, 0))


# --- layout determinism ------------------------------------------------------


def test_same_inputs_same_layout() -> None:
    a = _init({"mines": 5}, 7)
    b = _init({"mines": 5}, 7)
    assert a["mine_positions"] == b["mine_positions"]


def test_layout_consumes_exactly_m_draws() -> None:
    """init draws exactly M positions from the 25-cell grid (one per mine)."""
    for mines in (1, 3, 5, 24):
        rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
        _game().init({"mines": mines}, rng, _cfg())
        assert rng.cursor == mines


def test_layout_is_m_distinct_cells_in_grid() -> None:
    for mines in (1, 3, 5, 12, 24):
        for nonce in range(20):
            pos = _init({"mines": mines}, nonce)["mine_positions"]
            assert len(pos) == mines
            assert len(set(pos)) == mines
            assert all(0 <= c <= 24 for c in pos)


def test_distinct_nonces_diverge() -> None:
    layouts = {tuple(_init({"mines": 5}, n)["mine_positions"]) for n in range(30)}
    assert len(layouts) > 1


# --- the state machine -------------------------------------------------------


def _safe_cells(state: dict[str, Any]) -> list[int]:
    mines = set(state["mine_positions"])
    return [c for c in range(25) if c not in mines]


def test_safe_reveal_advances_k_and_reports_projection() -> None:
    state = _init({"mines": 3}, 1)
    safe = _safe_cells(state)
    state, outcome = _step(state, {"op": "reveal", "cell": safe[0]})
    assert outcome is not None
    assert outcome.detail["safe"] is True
    assert outcome.detail["k"] == 1
    assert outcome.detail["cell"] == safe[0]
    assert outcome.detail["status"] == "ACTIVE"
    assert outcome.detail["currentMultiplier"] > 1.0
    assert outcome.detail["nextMultiplier"] > outcome.detail["currentMultiplier"]


def test_revealing_a_mine_loses_the_round() -> None:
    state = _init({"mines": 5}, 2)
    mine = state["mine_positions"][0]
    state, outcome = _step(state, {"op": "reveal", "cell": mine})
    assert outcome is not None
    assert outcome.detail["safe"] is False
    assert outcome.detail["status"] == "LOST"
    assert outcome.multiplier == 0.0


def test_re_revealing_a_safe_cell_is_a_noop() -> None:
    """Intrinsic idempotency: re-revealing an already-safe cell does not advance k."""
    state = _init({"mines": 3}, 4)
    safe = _safe_cells(state)
    state, first = _step(state, {"op": "reveal", "cell": safe[0]})
    again_state, again = _step(state, {"op": "reveal", "cell": safe[0]})
    assert again is not None and first is not None
    assert again.detail["k"] == first.detail["k"] == 1
    assert again_state["revealed"] == state["revealed"]


def test_cashout_after_reveals_settles_cashed_out() -> None:
    state = _init({"mines": 3}, 5)
    safe = _safe_cells(state)
    for cell in safe[:2]:
        state, _ = _step(state, {"op": "reveal", "cell": cell})
    state, outcome = _step(state, {"op": "cashout"})
    assert outcome is not None
    assert outcome.detail["status"] == "CASHED_OUT"
    assert outcome.detail["k"] == 2
    assert outcome.multiplier > 1.0


def test_cashout_requires_at_least_one_reveal() -> None:
    state = _init({"mines": 3}, 6)
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "cashout"})


def test_no_action_after_terminal_lost() -> None:
    state = _init({"mines": 5}, 8)
    mine = state["mine_positions"][0]
    state, _ = _step(state, {"op": "reveal", "cell": mine})
    safe = _safe_cells(state)
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "reveal", "cell": safe[0]})
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "cashout"})


def test_no_action_after_terminal_cashed_out() -> None:
    state = _init({"mines": 3}, 9)
    safe = _safe_cells(state)
    state, _ = _step(state, {"op": "reveal", "cell": safe[0]})
    state, _ = _step(state, {"op": "cashout"})
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "reveal", "cell": safe[1]})


def test_unknown_op_and_bad_cell_rejected() -> None:
    state = _init({"mines": 3}, 10)
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "explode", "cell": 0})
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "reveal", "cell": 25})
    with pytest.raises(InvalidBetInput):
        _step(state, {"op": "reveal", "cell": -1})


# --- the input fence (validate_input) ---------------------------------------


@pytest.mark.parametrize("ok", [{"mines": 1}, {"mines": 3}, {"mines": 12}, {"mines": 24}])
def test_validate_input_accepts_valid(ok: dict[str, Any]) -> None:
    assert _game().validate_input(ok, _cfg()) is None


@pytest.mark.parametrize(
    "bad",
    [
        {},  # missing mines
        {"mines": 0},  # too few
        {"mines": 25},  # too many (only 24 mines leave 1 safe cell)
        {"mines": -1},  # negative
        {"mines": 3.0},  # non-integer
        {"mines": "3"},  # non-integer
        {"mines": True},  # bool is not a valid count
    ],
)
def test_validate_input_rejects_invalid(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _cfg())
