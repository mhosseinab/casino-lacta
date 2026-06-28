"""S21 — the data-driven slot framework: strips → grid → lines/ways evaluation.

The framework is config-driven (no machine ships until S23): a tiny KNOWN config
with HAND-COMPUTED wins proves the evaluator, line/ways correctness covers
wild/scatter/no-win, and determinism proves the outcome is a pure function of the
seeded stream. RTP emerges from strip weights + paytable — never a clamp (S23
wires a real machine + its RTP gate).

Outcome.multiplier convention (relative to TOTAL stake):
  * a paytable entry is a PER-LINE multiplier; total line contribution is
    ``Σ line_mult / num_lines`` (stake is split evenly across paylines);
  * ways/scatter entries are multipliers on the TOTAL bet, added directly.
The 2-line test (``test_lines_normalized_by_line_count``) is what pins the ÷num_lines
division — S23's config author and RTP gate rely on this convention.
"""

from __future__ import annotations

from typing import Any

from engine.rng import create_rng
from engine.slots.framework import SlotMachine
from engine.types import GameConfig, Outcome


class SequenceRng:
    """A scripted ``RngStream``: ``next()`` returns the next pre-set float.

    Lets a test place the grid exactly (``stop = floor(f · len(strip))``), so the
    expected win is hand-computable. Tracks ``cursor`` to assert draw count.
    """

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.cursor = 0

    def next(self) -> float:
        value = self._values[self.cursor]
        self.cursor += 1
        return value


def _cfg(params: dict[str, Any]) -> GameConfig:
    return GameConfig(edge=0.04, params=params)


# A 3-reel × 1-row single-payline machine. Strips of length 3 → stop = floor(f·3):
#   f=0.0 → index 0, f=0.4 → index 1, f=0.7 → index 2.
#   reel0 strip: [A, B, W]   reel1 strip: [A, B, S]   reel2 strip: [A, B, W]
# paytable (per-line): A→{3:10, 2:2}, B→{3:5}; wild=W, scatter=S.
_LINES_PARAMS: dict[str, Any] = {
    "reels": 3,
    "rows": 1,
    "mode": "lines",
    "strips": [["A", "B", "W"], ["A", "B", "S"], ["A", "B", "W"]],
    "symbols": ["A", "B", "W", "S"],
    "paytable": {"A": {"3": 10.0, "2": 2.0}, "B": {"3": 5.0}},
    "paylines": [[0, 0, 0]],
    "wild": "W",
    "scatter": "S",
}


def _play_lines(values: list[float]) -> Outcome:
    machine = SlotMachine()
    return machine.play({}, SequenceRng(values), _cfg(_LINES_PARAMS))


# --- known-config wins (hand-computed) --------------------------------------


def test_three_of_a_kind_pays_full_line() -> None:
    # stops [0,0,0] → grid column symbols [A, A, A]; line [A,A,A] → A×3 = 10×.
    # num_lines=1 → total = 10/1 = 10.0.
    out = _play_lines([0.0, 0.0, 0.0])
    assert out.multiplier == 10.0
    assert out.detail["grid"] == [["A"], ["A"], ["A"]]
    assert out.detail["lineWins"] == [
        {"line": 0, "symbol": "A", "count": 3, "multiplier": 10.0}
    ]


def test_wild_substitutes_for_the_line_symbol() -> None:
    # stops [2,0,0] → [W, A, A]; first non-wild = A, W substitutes → A×3 = 10×.
    out = _play_lines([0.7, 0.0, 0.0])
    assert out.detail["grid"] == [["W"], ["A"], ["A"]]
    assert out.multiplier == 10.0
    assert out.detail["lineWins"] == [
        {"line": 0, "symbol": "A", "count": 3, "multiplier": 10.0}
    ]


def test_partial_run_pays_the_shorter_count() -> None:
    # stops [0,0,1] → [A, A, B]; run of A breaks at reel2 → A×2 = 2×.
    out = _play_lines([0.0, 0.0, 0.4])
    assert out.detail["grid"] == [["A"], ["A"], ["B"]]
    assert out.multiplier == 2.0
    assert out.detail["lineWins"] == [
        {"line": 0, "symbol": "A", "count": 2, "multiplier": 2.0}
    ]


def test_no_win_when_first_reel_is_alone() -> None:
    # stops [1,0,0] → [B, A, A]; B run = 1, no paytable["B"]["1"] → 0.
    out = _play_lines([0.4, 0.0, 0.0])
    assert out.detail["grid"] == [["B"], ["A"], ["A"]]
    assert out.multiplier == 0.0
    assert out.detail["lineWins"] == []


# --- scatter (pays on TOTAL bet, anywhere, not on a line) --------------------

# reel0 [S, A], reel1 [S, A], reel2 [A, B] (len 2 → stop = floor(f·2)).
_SCATTER_PARAMS: dict[str, Any] = {
    "reels": 3,
    "rows": 1,
    "mode": "lines",
    "strips": [["S", "A"], ["S", "A"], ["A", "B"]],
    "symbols": ["A", "B", "S"],
    "paytable": {"A": {"3": 10.0}},
    "paylines": [[0, 0, 0]],
    "wild": None,
    "scatter": "S",
    "scatterPaytable": {"2": 3.0, "3": 20.0},
}


def test_scatter_pays_on_total_bet_added_directly() -> None:
    # stops [0,0,0] → [S, S, A]; 2 scatters → 3× total bet; line [S,S,A] has no
    # A run from reel0 → 0 line win. total = 0 + 3.0 = 3.0.
    machine = SlotMachine()
    out = machine.play({}, SequenceRng([0.0, 0.0, 0.0]), _cfg(_SCATTER_PARAMS))
    assert out.detail["grid"] == [["S"], ["S"], ["A"]]
    assert out.detail["lineWins"] == []
    assert out.detail["scatterWin"] == {"symbol": "S", "count": 2, "multiplier": 3.0}
    assert out.multiplier == 3.0


# --- ÷num_lines normalization (the convention S23 depends on) ----------------

# 3 reels × 2 rows, every strip all-A → grid is all A; two horizontal lines.
_TWO_LINE_PARAMS: dict[str, Any] = {
    "reels": 3,
    "rows": 2,
    "mode": "lines",
    "strips": [["A", "A"], ["A", "A"], ["A", "A"]],
    "symbols": ["A"],
    "paytable": {"A": {"3": 10.0}},
    "paylines": [[0, 0, 0], [1, 1, 1]],
    "wild": None,
    "scatter": None,
}


def test_lines_normalized_by_line_count() -> None:
    # both lines hit A×3 = 10× each → Σ = 20; ÷ num_lines(2) → 10.0.
    machine = SlotMachine()
    out = machine.play({}, SequenceRng([0.0, 0.0, 0.0]), _cfg(_TWO_LINE_PARAMS))
    assert len(out.detail["lineWins"]) == 2
    assert out.multiplier == 10.0


# --- ways-to-win evaluation --------------------------------------------------

# 3 reels × 2 rows, ways mode. Strips len 2 → stop = floor(f·2).
#   reel0 [A, A] → column [A, A] (2 A's, any stop)
#   reel1 [A, B] → stop0 → [A, B] (1 A on row0)
#   reel2 [A, B] → stop0 → [A, B] (1 A on row0)
_WAYS_PARAMS: dict[str, Any] = {
    "reels": 3,
    "rows": 2,
    "mode": "ways",
    "strips": [["A", "A"], ["A", "B"], ["A", "B"]],
    "symbols": ["A", "B"],
    "paytable": {"A": {"3": 4.0}},
    "wild": None,
    "scatter": None,
}


def test_ways_multiplies_by_number_of_ways() -> None:
    # grid [[A,A],[A,B],[A,B]]: A on reel0×2, reel1×1, reel2×1, consecutive 3 reels
    # → ways = 2·1·1 = 2; A×3 pays 4× → 4·2 = 8× total bet (no ÷lines).
    machine = SlotMachine()
    out = machine.play({}, SequenceRng([0.0, 0.0, 0.0]), _cfg(_WAYS_PARAMS))
    assert out.detail["grid"] == [["A", "A"], ["A", "B"], ["A", "B"]]
    assert out.detail["wayWins"] == [
        {"symbol": "A", "reels": 3, "ways": 2, "multiplier": 8.0}
    ]
    assert out.multiplier == 8.0


def test_ways_no_win_when_run_too_short() -> None:
    # reel0 [A,A], reel1 [B,B], reel2 [A,A]: A run breaks at reel1 → run=1, no
    # paytable["A"]["1"] → no way win, multiplier 0.
    params = {
        "reels": 3,
        "rows": 2,
        "mode": "ways",
        "strips": [["A", "A"], ["B", "B"], ["A", "A"]],
        "symbols": ["A", "B"],
        "paytable": {"A": {"3": 4.0}},
        "wild": None,
        "scatter": None,
    }
    out = SlotMachine().play({}, SequenceRng([0.0, 0.0, 0.0]), _cfg(params))
    assert out.detail["wayWins"] == []
    assert out.multiplier == 0.0


# --- mechanics: stop index, wraparound, draw count, determinism --------------


def test_play_consumes_exactly_one_draw_per_reel() -> None:
    rng = SequenceRng([0.0, 0.0, 0.0])
    SlotMachine().play({}, rng, _cfg(_LINES_PARAMS))
    assert rng.cursor == 3  # one stop draw per reel, regardless of wins


def test_grid_window_wraps_around_the_strip() -> None:
    # rows=2, reel strip [A, B] len 2; stop at index 1 → window [B, A] (wraps).
    params = {
        "reels": 1,
        "rows": 2,
        "mode": "lines",
        "strips": [["A", "B"]],
        "symbols": ["A", "B"],
        "paytable": {},
        "paylines": [],
        "wild": None,
        "scatter": None,
    }
    out = SlotMachine().play({}, SequenceRng([0.7]), _cfg(params))  # stop=floor(.7·2)=1
    assert out.detail["grid"] == [["B", "A"]]


def test_deterministic_same_seed_same_outcome() -> None:
    cfg = _cfg(_LINES_PARAMS)
    a = SlotMachine().play({}, create_rng(b"slot-seed", "client", 7), cfg)
    b = SlotMachine().play({}, create_rng(b"slot-seed", "client", 7), cfg)
    assert a == b


def test_distinct_nonces_can_diverge() -> None:
    cfg = _cfg(_LINES_PARAMS)
    grids = {
        tuple(tuple(col) for col in SlotMachine().play(
            {}, create_rng(b"slot-seed", "client", n), cfg
        ).detail["grid"])
        for n in range(40)
    }
    assert len(grids) > 1
