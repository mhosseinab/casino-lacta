"""S14 — HiLo: per-rank step probs/multipliers, cumulative compounding, the card
mapping, full-sequence determinism, and the state machine (pure, no IO/clock/random).

The whole sequence is a pure function of ``(server_seed, client_seed, nonce, cursor)``
(spec §A.7): ``init`` draws the first shown card at cursor 0; each ``guess`` draws the
next card at the next cursor from the SAME stream. ``cashout`` settles the cumulative
multiplier; the engine returns it as a float (money is floored once, in ``app``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from engine.games.hilo import draw_rank, p_win, step_multiplier
from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InvalidBetInput, RngStream, StatefulGame

GAME_ID = "originals.hilo"
EDGE = 0.01
SERVER_SEED = b"hilo-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> StatefulGame:
    return cast("StatefulGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


@dataclass
class _FakeRng:
    """A deterministic stub stream that yields preset floats — to drive specific cards
    (a tie, a guaranteed loss) without searching the real HMAC stream."""

    values: list[float]
    i: int = field(default=0)

    def next(self) -> float:
        v = self.values[self.i]
        self.i += 1
        return v


def _f_for_card(card_index: int) -> float:
    """A float that maps to a given ``cardIndex`` via ``floor(f*52)`` (mid-bucket)."""
    return (card_index + 0.5) / 52


# --- the card → rank mapping (uniform over 1..13) ----------------------------


def test_draw_rank_maps_card_index_to_rank() -> None:
    """``rank = floor(f*52)//4 + 1`` — each rank owns exactly its four suits."""
    for card_index in range(52):
        rng = _FakeRng([_f_for_card(card_index)])
        assert draw_rank(cast("RngStream", rng)) == card_index // 4 + 1


def test_draw_rank_is_uniform_over_1_to_13() -> None:
    """Every rank 1..13 is reachable and exactly 4 card indices map to each."""
    counts = {r: 0 for r in range(1, 14)}
    for card_index in range(52):
        rng = _FakeRng([_f_for_card(card_index)])
        counts[draw_rank(cast("RngStream", rng))] += 1
    assert set(counts) == set(range(1, 14))
    assert all(c == 4 for c in counts.values())


def test_draw_rank_stays_in_range_on_real_stream() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    for _ in range(2000):
        assert 1 <= draw_rank(rng) <= 13


# --- per-rank probabilities + step multipliers -------------------------------


def test_side_probabilities_per_rank() -> None:
    """p(High≥)=(14-r)/13, p(Low≤)=r/13 — and they sum to >1 (tie wins either side)."""
    for r in range(1, 14):
        assert p_win(r, "HIGHER") == pytest.approx((14 - r) / 13, rel=1e-12)
        assert p_win(r, "LOWER") == pytest.approx(r / 13, rel=1e-12)
        # The tie rank is double-counted, so the two side probabilities sum to 14/13.
        assert p_win(r, "HIGHER") + p_win(r, "LOWER") == pytest.approx(14 / 13, rel=1e-12)


def test_step_multiplier_per_rank_matches_independent_formula() -> None:
    """``(1-edge)/p`` for every rank, computed an independent way: (1-edge)·13/(14-r)
    for HIGHER and (1-edge)·13/r for LOWER. Fails on any mistuned multiplier."""
    for r in range(1, 14):
        assert step_multiplier(r, "HIGHER", EDGE) == pytest.approx(
            (1 - EDGE) * 13 / (14 - r), rel=1e-12
        )
        assert step_multiplier(r, "LOWER", EDGE) == pytest.approx(
            (1 - EDGE) * 13 / r, rel=1e-12
        )


# The spec §A.7 reference table (edge = 0.01), rounded to 3 dp.
_REFERENCE = {
    (1, "HIGHER"): 0.990, (1, "LOWER"): 12.870,
    (5, "HIGHER"): 1.430, (5, "LOWER"): 2.574,
    (7, "HIGHER"): 1.839, (7, "LOWER"): 1.839,
    (9, "HIGHER"): 2.574, (9, "LOWER"): 1.430,
    (13, "HIGHER"): 12.870, (13, "LOWER"): 0.990,
}


@pytest.mark.parametrize(("rank", "side"), sorted(_REFERENCE))
def test_matches_spec_reference_multipliers(rank: int, side: str) -> None:
    assert round(step_multiplier(rank, side, EDGE), 3) == _REFERENCE[(rank, side)]


def test_each_step_ev_is_one_minus_edge() -> None:
    """EV per step = p(side)·stepMultiplier == 1-edge EXACTLY (pre-rounding), any rank."""
    for r in range(1, 14):
        for side in ("HIGHER", "LOWER"):
            ev = p_win(r, side) * step_multiplier(r, side, EDGE)
            assert ev == pytest.approx(1 - EDGE, abs=1e-12)


# --- cumulative compounding (binds the step path to the per-step multiplier) --


def test_cumulative_is_product_of_per_step_multipliers() -> None:
    """A guaranteed-winning HIGHER sequence (non-decreasing ranks) compounds: at each
    won guess the engine's currentMultiplier equals the running product of
    ``step_multiplier(shownRank, side, edge)``. A mistuned multiplier or a broken
    product breaks this. ``_FakeRng`` makes the sequence deterministic regardless of
    the real stream."""
    # init rank 1, then reveal ranks 1, 5, 9, 13 → HIGHER wins every step (revealed >=
    # current shown), exercising shown ranks 1, 1, 5, 9.
    rng = _FakeRng(
        [_f_for_card(0), _f_for_card(0), _f_for_card(16), _f_for_card(32), _f_for_card(48)]
    )
    state = _game().init({}, cast("RngStream", rng), _cfg())
    assert state["shown_rank"] == 1
    expected = 1.0
    for _ in range(4):
        shown = state["shown_rank"]
        state, outcome = _game().step(
            state, {"op": "guess", "side": "HIGHER"}, cast("RngStream", rng)
        )
        assert outcome is not None
        assert outcome.detail["status"] == "ACTIVE"  # every step wins
        expected *= step_multiplier(shown, "HIGHER", EDGE)
        assert outcome.detail["currentMultiplier"] == pytest.approx(expected, rel=1e-12)
        assert outcome.multiplier == pytest.approx(expected, rel=1e-12)
    assert state["steps"] == 4


# --- full-sequence determinism ----------------------------------------------


def _run(nonce: int, sides: list[str]) -> list[dict[str, Any]]:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    state = _game().init({}, rng, _cfg())
    details: list[dict[str, Any]] = []
    for side in sides:
        if state["status"] != "ACTIVE":
            break
        state, outcome = _game().step(state, {"op": "guess", "side": side}, rng)
        assert outcome is not None
        details.append(outcome.detail)
    return details


def test_same_inputs_same_full_sequence() -> None:
    sides = ["HIGHER", "LOWER", "HIGHER", "HIGHER", "LOWER", "HIGHER"]
    a = _run(7, sides)
    b = _run(7, sides)
    assert [d["revealedRank"] for d in a] == [d["revealedRank"] for d in b]
    assert [d.get("currentMultiplier") for d in a] == [d.get("currentMultiplier") for d in b]


def test_distinct_nonces_diverge() -> None:
    sides = ["HIGHER"] * 10
    seqs = {tuple(d["revealedRank"] for d in _run(n, sides)) for n in range(30)}
    assert len(seqs) > 1


# --- tie semantics + the state machine ---------------------------------------


def test_tie_counts_as_a_win_for_either_side() -> None:
    """A revealed card equal to the shown card wins for HIGHER and for LOWER."""
    for side in ("HIGHER", "LOWER"):
        # init draws card index 20 → rank 6; guess draws card index 21 → rank 6 (tie).
        rng = _FakeRng([_f_for_card(20), _f_for_card(21)])
        state = _game().init({}, cast("RngStream", rng), _cfg())
        assert state["shown_rank"] == 6
        state, outcome = _game().step(state, {"op": "guess", "side": side}, cast("RngStream", rng))
        assert outcome is not None
        assert outcome.detail["revealedRank"] == 6
        assert outcome.detail["won"] is True
        assert outcome.detail["status"] == "ACTIVE"


def test_wrong_guess_loses_the_round() -> None:
    # shown rank 10 (index 36), reveal rank 1 (index 0): a HIGHER guess loses.
    rng = _FakeRng([_f_for_card(36), _f_for_card(0)])
    state = _game().init({}, cast("RngStream", rng), _cfg())
    assert state["shown_rank"] == 10
    state, outcome = _game().step(state, {"op": "guess", "side": "HIGHER"}, cast("RngStream", rng))
    assert outcome is not None
    assert outcome.detail["won"] is False
    assert outcome.detail["status"] == "LOST"
    assert outcome.multiplier == 0.0


def test_cashout_after_a_win_settles() -> None:
    # init rank 1 (index 0), reveal rank 13 (index 48): HIGHER wins for certain.
    rng = _FakeRng([_f_for_card(0), _f_for_card(48)])
    state = _game().init({}, cast("RngStream", rng), _cfg())
    state, out = _game().step(state, {"op": "guess", "side": "HIGHER"}, cast("RngStream", rng))
    assert out is not None and out.detail["status"] == "ACTIVE"
    state, cash = _game().step(state, {"op": "cashout"}, cast("RngStream", rng))
    assert cash is not None
    assert cash.detail["status"] == "CASHED_OUT"
    assert cash.detail["steps"] == 1
    assert cash.multiplier == pytest.approx(out.detail["currentMultiplier"], rel=1e-12)


def test_cashout_requires_at_least_one_guess() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 4)
    state = _game().init({}, rng, _cfg())
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "cashout"}, rng)


def test_no_action_after_terminal_cashed_out() -> None:
    rng = _FakeRng([_f_for_card(0), _f_for_card(48), _f_for_card(0)])
    state = _game().init({}, cast("RngStream", rng), _cfg())
    state, _ = _game().step(state, {"op": "guess", "side": "HIGHER"}, cast("RngStream", rng))
    state, _ = _game().step(state, {"op": "cashout"}, cast("RngStream", rng))
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "guess", "side": "HIGHER"}, cast("RngStream", rng))


def test_unknown_op_and_bad_side_rejected() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 5)
    state = _game().init({}, rng, _cfg())
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "fold"}, rng)
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "guess", "side": "SIDEWAYS"}, rng)
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "guess"}, rng)


def test_validate_input_accepts_empty() -> None:
    assert _game().validate_input({}, _cfg()) is None


# --- public_view (the client-safe snapshot for /bet open + /state) -----------


def test_public_view_surfaces_shown_card() -> None:
    """HiLo holds no secret: the snapshot surfaces the shown card (so the opening is
    playable) and the cumulative multiplier."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 7)
    state = _game().init({}, rng, _cfg())
    view = _game().public_view(state)
    assert view["status"] == "ACTIVE"
    assert view["shownRank"] == state["shown_rank"]
    assert view["currentMultiplier"] == state["cumulative"]
    assert view["steps"] == 0


def test_public_view_tracks_shown_card_after_a_guess() -> None:
    """After a winning guess the snapshot's shown card is the revealed card."""
    rng = _FakeRng([_f_for_card(0), _f_for_card(48)])  # shown rank 1, reveal rank 13
    state = _game().init({}, cast("RngStream", rng), _cfg())
    state, outcome = _game().step(state, {"op": "guess", "side": "HIGHER"}, cast("RngStream", rng))
    assert outcome is not None
    view = _game().public_view(state)
    assert view["shownRank"] == 13 == outcome.detail["revealedRank"]
    assert view["steps"] == 1
    assert view["currentMultiplier"] == pytest.approx(
        outcome.detail["currentMultiplier"], rel=1e-12
    )
