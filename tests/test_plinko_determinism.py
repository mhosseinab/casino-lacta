"""S13 — plinko determinism + path↔bin tie + input fence: pure outcome logic.

The bin is a pure function of the seeded stream: ``rightBounces = Σ[f < 0.5]`` over
``R`` draws and ``bin = rightBounces`` (spec §A.5). The rendered ``path`` is
advisory but MUST end in the server bin (``path.count("R") == bin``). The
``validate_input`` fence rejects structurally invalid ``(rows, risk)`` selections
deterministically, deriving the accepted set from ``cfg.params`` (no buried literals).
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import InstantGame, InvalidBetInput, Outcome

GAME_ID = "originals.plinko"
SERVER_SEED = b"plinko-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> InstantGame:
    return cast("InstantGame", load_game(GAME_ID))


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, default_config(GAME_ID))


def test_same_inputs_same_outcome() -> None:
    a = _play({"rows": 16, "risk": "HIGH"}, 7)
    b = _play({"rows": 16, "risk": "HIGH"}, 7)
    assert a == b  # frozen Outcome dataclass: multiplier + detail (incl. path)


def test_distinct_nonces_diverge() -> None:
    """The stream advances with nonce, so the landing bin varies across bets."""
    bins = {_play({"rows": 16, "risk": "HIGH"}, n).detail["bin"] for n in range(40)}
    assert len(bins) > 1


@pytest.mark.parametrize("rows", [8, 12, 16])
@pytest.mark.parametrize("risk", ["LOW", "MEDIUM", "HIGH"])
def test_path_ends_in_server_bin(rows: int, risk: str) -> None:
    """Rendered path is advisory but MUST end in the authoritative server bin.

    ``len(path) == rows``, the path is L/R only, and the number of right bounces
    equals both ``rightBounces`` and ``bin`` — the renderer cannot disagree with
    the server outcome (spec §A.5).
    """
    for nonce in range(30):
        out = _play({"rows": rows, "risk": risk}, nonce)
        path = out.detail["path"]
        bin_index = out.detail["bin"]
        assert len(path) == rows
        assert set(path) <= {"L", "R"}
        assert path.count("R") == bin_index
        assert out.detail["rightBounces"] == bin_index
        assert 0 <= bin_index <= rows


@pytest.mark.parametrize("rows", [8, 12, 16])
def test_consumes_exactly_rows_draws(rows: int) -> None:
    """play advances the stream by exactly ``rows`` draws — one bounce per row."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    _game().play({"rows": rows, "risk": "MEDIUM"}, rng, default_config(GAME_ID))
    assert rng.cursor == rows


@pytest.mark.parametrize("rows", [8, 12, 16])
@pytest.mark.parametrize("risk", ["LOW", "MEDIUM", "HIGH"])
def test_multiplier_matches_published_table(rows: int, risk: str) -> None:
    """The settled multiplier is exactly the published table entry for the bin."""
    table = default_config(GAME_ID).params["tables"][str(rows)][risk]
    for nonce in range(20):
        out = _play({"rows": rows, "risk": risk}, nonce)
        assert out.multiplier == table[out.detail["bin"]]


# --- validate_input fence (derives the accepted set from cfg.params) ---


@pytest.mark.parametrize("bad", [{}, {"risk": "HIGH"}, {"rows": 16}])
def test_validate_rejects_missing_selection(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, default_config(GAME_ID))


@pytest.mark.parametrize("rows", [7, 10, 9, 32, 0, -8])
def test_validate_rejects_unsupported_rows(rows: int) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"rows": rows, "risk": "LOW"}, default_config(GAME_ID))


@pytest.mark.parametrize("rows", ["8", 8.0, True, None, [8]])
def test_validate_rejects_non_int_rows(rows: Any) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"rows": rows, "risk": "LOW"}, default_config(GAME_ID))


@pytest.mark.parametrize("risk", ["EXTREME", "low", "", 3, None])
def test_validate_rejects_unsupported_risk(risk: Any) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"rows": 16, "risk": risk}, default_config(GAME_ID))


@pytest.mark.parametrize("rows", [8, 12, 16])
@pytest.mark.parametrize("risk", ["LOW", "MEDIUM", "HIGH"])
def test_validate_accepts_supported_selection(rows: int, risk: str) -> None:
    assert _game().validate_input({"rows": rows, "risk": risk}, default_config(GAME_ID)) is None
