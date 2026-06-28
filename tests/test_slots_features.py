"""S22 — composable slot feature modules: free spins, hold-and-spin, pick bonus.

Each feature is a PURE function of (grid where relevant, the seeded ``rng`` stream,
its own config dict). The framework invokes them through one dispatcher,
:func:`apply_features`, which fires a feature iff its config-declared trigger
(scatter-style ``{symbol, count}`` over the base grid) is met. RTP emerges from the
config (trigger reach + the weighted prize/value strips), never a clamp.

Each prize/value selection mirrors the framework's reel-stop discipline: a strip is
a list, the draw index is ``floor(f · len(strip))`` (weighting = repeated entries),
so a scripted :class:`SequenceRng` places every outcome and the payouts below are
HAND-COMPUTED exactly. Determinism is proven by the HMAC stream (same seed → same
feature outcome).

Free-spins modeling note: a retrigger is modelled as a drawn prize-strip entry that
adds spins — the abstraction of "a scatter reappears during the feature and awards
more spins". hold-and-spin and pick-bonus are genuinely distinct mini-games (not reel
re-spins), so they are config-driven outright.
"""

from __future__ import annotations

from typing import Any

from engine.rng import create_rng
from engine.slots.features import (
    apply_features,
    free_spins,
    hold_and_spin,
    pick_bonus,
    trigger_fires,
)
from engine.slots.framework import SlotMachine
from engine.types import GameConfig


class SequenceRng:
    """A scripted ``RngStream``: ``next()`` returns the next pre-set float.

    Lets a test place every draw exactly (index = ``floor(f · len(strip))``), so the
    feature payout is hand-computable. ``cursor`` asserts the exact draw count.
    """

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.cursor = 0

    def next(self) -> float:
        value = self._values[self.cursor]
        self.cursor += 1
        return value


# --- trigger conditions ------------------------------------------------------


def test_trigger_fires_when_count_met() -> None:
    grid = [["S"], ["S"], ["S"]]
    assert trigger_fires(grid, {"symbol": "S", "count": 3}) is True


def test_trigger_fires_when_count_exceeded() -> None:
    grid = [["S", "S"], ["S"], ["A"]]
    assert trigger_fires(grid, {"symbol": "S", "count": 3}) is True


def test_trigger_does_not_fire_below_count() -> None:
    grid = [["S"], ["S"], ["A"]]
    assert trigger_fires(grid, {"symbol": "S", "count": 3}) is False


# --- free spins (retrigger-capable) ------------------------------------------

# prizeStrip len 3 → index = floor(f·3): f=0.0→0, f=0.4→1, f=0.7→2.
#   idx0 = 1.0× no retrigger, idx1 = 5.0× no retrigger, idx2 = 0.0× +1 spin.
_FREE_CFG: dict[str, Any] = {
    "spins": 2,
    "prizeStrip": [
        {"multiplier": 1.0, "retrigger": 0},
        {"multiplier": 5.0, "retrigger": 0},
        {"multiplier": 0.0, "retrigger": 1},
    ],
}


def test_free_spins_basic_accumulates_wins() -> None:
    # spins=2, draws [0.0, 0.0] → idx0 each (1.0×, no retrigger).
    #   spin1: total 1.0; spin2: total 2.0. 2 spins played, 2 draws.
    rng = SequenceRng([0.0, 0.0])
    total, detail = free_spins(rng, _FREE_CFG)
    assert total == 2.0
    assert detail == {"spinsPlayed": 2}
    assert rng.cursor == 2


def test_free_spins_retrigger_adds_a_spin() -> None:
    # draws [0.7, 0.0, 0.4]:
    #   spin1 idx2 → 0.0×, +1 spin (retrigger): total 0.0, remaining 2.
    #   spin2 idx0 → 1.0×:                       total 1.0, remaining 1.
    #   spin3 idx1 → 5.0×:                       total 6.0, remaining 0.
    # → 3 spins played (2 awarded + 1 retrigger), total 6.0, 3 draws.
    rng = SequenceRng([0.7, 0.0, 0.4])
    total, detail = free_spins(rng, _FREE_CFG)
    assert total == 6.0
    assert detail == {"spinsPlayed": 3}
    assert rng.cursor == 3


def test_free_spins_max_spins_guard_bounds_an_all_retrigger_strip() -> None:
    # A pathological strip whose only entry always retriggers would loop forever;
    # maxSpins caps the COUNT (never a per-spin multiplier), guaranteeing termination.
    cfg = {
        "spins": 1,
        "maxSpins": 4,
        "prizeStrip": [{"multiplier": 2.0, "retrigger": 1}],
    }
    rng = SequenceRng([0.0] * 10)
    total, detail = free_spins(rng, cfg)
    assert detail == {"spinsPlayed": 4}
    assert total == 8.0  # 4 spins × 2.0×
    assert rng.cursor == 4


def test_free_spins_deterministic_same_seed() -> None:
    a = free_spins(create_rng(b"fs-seed", "client", 11), _FREE_CFG)
    b = free_spins(create_rng(b"fs-seed", "client", 11), _FREE_CFG)
    assert a == b


# --- hold-and-spin (lock symbols, respins, reset on new locks) ---------------

# coinValueStrip len 2 → index floor(f·2): f<0.5→idx0, f>=0.5→idx1.
_HOLD_CFG: dict[str, Any] = {
    "coinSymbol": "C",
    "respins": 2,
    "coinChance": 0.5,
    "coinValueStrip": [2.0, 10.0],
}


def test_hold_and_spin_locks_respins_resets_and_pays_final_set() -> None:
    # grid 3 reels × 1 row, position 0 already a coin "C".
    # draws:
    #   [0] 0.0 → initial coin@pos0 value idx0 = 2.0
    #   respin round 1 (respins_left=2), unlocked {1,2}:
    #     [1] 0.0 < 0.5 → pos1 locks; [2] 0.9 → value idx1 = 10.0
    #     [3] 0.9 >= 0.5 → pos2 no lock
    #     new_locks=1 → respins_left reset to 2
    #   respin round 2 (respins_left=2), unlocked {2}:
    #     [4] 0.9 → no lock; new_locks=0 → respins_left = 1
    #   respin round 3 (respins_left=1), unlocked {2}:
    #     [5] 0.9 → no lock; new_locks=0 → respins_left = 0 → stop
    # final locked values: pos0=2.0, pos1=10.0 → total 12.0, 2 coins, 6 draws.
    grid = [["C"], ["A"], ["A"]]
    rng = SequenceRng([0.0, 0.0, 0.9, 0.9, 0.9, 0.9])
    total, detail = hold_and_spin(grid, rng, _HOLD_CFG)
    assert total == 12.0
    assert detail == {"coins": 2, "lockedValues": [2.0, 10.0]}
    assert rng.cursor == 6


def test_hold_and_spin_respins_exhaust_without_new_locks() -> None:
    # grid 2 positions, pos0 coin. respins=1, value strip [3.0] (len1 → always idx0).
    #   [0] 0.0 → initial coin@pos0 value 3.0
    #   round 1 (respins_left=1), unlocked {1}: [1] 0.9 → no lock → respins_left 0 → stop
    # total 3.0, 1 coin, 2 draws.
    grid = [["C"], ["A"]]
    cfg = {"coinSymbol": "C", "respins": 1, "coinChance": 0.5, "coinValueStrip": [3.0]}
    rng = SequenceRng([0.0, 0.9])
    total, detail = hold_and_spin(grid, rng, cfg)
    assert total == 3.0
    assert detail == {"coins": 1, "lockedValues": [3.0]}
    assert rng.cursor == 2


def test_hold_and_spin_deterministic_same_seed() -> None:
    grid = [["C"], ["A"], ["A"]]
    a = hold_and_spin(grid, create_rng(b"has-seed", "client", 3), _HOLD_CFG)
    b = hold_and_spin(grid, create_rng(b"has-seed", "client", 3), _HOLD_CFG)
    assert a == b


# --- pick bonus (choose-to-reveal until a terminator) ------------------------

# Pick WITHOUT replacement; index = floor(f · len(remaining pool)).
_PICK_CFG: dict[str, Any] = {
    "prizePool": [
        {"multiplier": 2.0, "terminator": False},
        {"multiplier": 5.0, "terminator": False},
        {"multiplier": 0.0, "terminator": True},
    ],
}


def test_pick_bonus_reveals_until_terminator() -> None:
    # pool len 3 → 2; draws all 0.0 → idx0 each time:
    #   pick1 → P0 (2.0, not term): total 2.0; pool → [P1, P2]
    #   pick2 → P1 (5.0, not term): total 7.0; pool → [P2]
    #   pick3 → P2 (terminator): stop. total 7.0, 3 draws.
    rng = SequenceRng([0.0, 0.0, 0.0])
    total, detail = pick_bonus(rng, _PICK_CFG)
    assert total == 7.0
    assert detail == {"reveals": [2.0, 5.0], "terminated": True}
    assert rng.cursor == 3


def test_pick_bonus_immediate_terminator() -> None:
    # draw 0.9 → idx floor(0.9·3)=2 → P2 terminator immediately. total 0.0, 1 draw.
    rng = SequenceRng([0.9])
    total, detail = pick_bonus(rng, _PICK_CFG)
    assert total == 0.0
    assert detail == {"reveals": [], "terminated": True}
    assert rng.cursor == 1


def test_pick_bonus_exhausts_pool_without_terminator() -> None:
    # pool with no terminator → reveals all, stops when empty.
    cfg = {
        "prizePool": [
            {"multiplier": 2.0, "terminator": False},
            {"multiplier": 3.0, "terminator": False},
        ]
    }
    rng = SequenceRng([0.0, 0.0])
    total, detail = pick_bonus(rng, cfg)
    assert total == 5.0
    assert detail == {"reveals": [2.0, 3.0], "terminated": False}
    assert rng.cursor == 2


def test_pick_bonus_deterministic_same_seed() -> None:
    a = pick_bonus(create_rng(b"pb-seed", "client", 5), _PICK_CFG)
    b = pick_bonus(create_rng(b"pb-seed", "client", 5), _PICK_CFG)
    assert a == b


# --- dispatcher: framework fires a feature iff its trigger is met -------------


def test_apply_features_fires_matching_feature() -> None:
    grid = [["S"], ["S"], ["S"]]
    params = {
        "features": [
            {
                "type": "freeSpins",
                "trigger": {"symbol": "S", "count": 3},
                "spins": 1,
                "prizeStrip": [{"multiplier": 5.0, "retrigger": 0}],
            }
        ]
    }
    total, results = apply_features(grid, SequenceRng([0.0]), params)
    assert total == 5.0
    assert results == [{"type": "freeSpins", "multiplier": 5.0, "spinsPlayed": 1}]


def test_apply_features_dispatches_hold_and_spin() -> None:
    # Exercises the holdAndSpin dispatch branch (incl. passing the grid through).
    # grid 2 positions, pos0 coin "C"; respins 1, value strip [3.0].
    # draws [0.0 init value 3.0, 0.9 no lock] → total 3.0.
    grid = [["C"], ["A"]]
    params = {
        "features": [
            {
                "type": "holdAndSpin",
                "trigger": {"symbol": "C", "count": 1},
                "coinSymbol": "C",
                "respins": 1,
                "coinChance": 0.5,
                "coinValueStrip": [3.0],
            }
        ]
    }
    total, results = apply_features(grid, SequenceRng([0.0, 0.9]), params)
    assert total == 3.0
    assert results == [
        {"type": "holdAndSpin", "multiplier": 3.0, "coins": 1, "lockedValues": [3.0]}
    ]


def test_apply_features_dispatches_pick_bonus() -> None:
    # Exercises the pickBonus dispatch branch.
    # pool [P0(2.0,F), P1(term)]; draws [0.0 pick P0, 0.0 pick terminator] → 2.0.
    grid = [["B"]]
    params = {
        "features": [
            {
                "type": "pickBonus",
                "trigger": {"symbol": "B", "count": 1},
                "prizePool": [
                    {"multiplier": 2.0, "terminator": False},
                    {"multiplier": 0.0, "terminator": True},
                ],
            }
        ]
    }
    total, results = apply_features(grid, SequenceRng([0.0, 0.0]), params)
    assert total == 2.0
    assert results == [
        {"type": "pickBonus", "multiplier": 2.0, "reveals": [2.0], "terminated": True}
    ]


def test_apply_features_skips_unmet_trigger() -> None:
    grid = [["S"], ["S"], ["A"]]
    params = {
        "features": [
            {
                "type": "freeSpins",
                "trigger": {"symbol": "S", "count": 3},
                "spins": 1,
                "prizeStrip": [{"multiplier": 5.0, "retrigger": 0}],
            }
        ]
    }
    total, results = apply_features(grid, SequenceRng([0.0]), params)
    assert total == 0.0
    assert results == []


def test_apply_features_no_features_key_is_noop() -> None:
    total, results = apply_features([["A"]], SequenceRng([]), {})
    assert total == 0.0
    assert results == []


# --- integration: SlotMachine.play invokes the feature on trigger ------------


def _cfg(params: dict[str, Any]) -> GameConfig:
    return GameConfig(edge=0.04, params=params)


# 3 reels × 1 row, each strip len 1 → stop always 0 → grid [[S],[S],[S]] (3 draws).
# scatter S with no scatterPaytable → 0 scatter pay; S has no line pay → 0 base.
# free-spins triggers on 3 S, 1 spin, prize 5.0× (4th draw).
_FEATURE_MACHINE: dict[str, Any] = {
    "reels": 3,
    "rows": 1,
    "mode": "lines",
    "strips": [["S"], ["S"], ["S"]],
    "symbols": ["S"],
    "paytable": {},
    "paylines": [[0, 0, 0]],
    "wild": None,
    "scatter": "S",
    "features": [
        {
            "type": "freeSpins",
            "trigger": {"symbol": "S", "count": 3},
            "spins": 1,
            "prizeStrip": [{"multiplier": 5.0, "retrigger": 0}],
        }
    ],
}


def test_machine_play_applies_triggered_feature() -> None:
    # base draws [0.0,0.0,0.0] → grid [[S],[S],[S]]; 4th draw 0.0 → free-spin 5.0×.
    out = SlotMachine().play({}, SequenceRng([0.0, 0.0, 0.0, 0.0]), _cfg(_FEATURE_MACHINE))
    assert out.detail["grid"] == [["S"], ["S"], ["S"]]
    assert out.detail["features"] == [
        {"type": "freeSpins", "multiplier": 5.0, "spinsPlayed": 1}
    ]
    assert out.multiplier == 5.0


def test_machine_play_no_feature_key_omitted_when_untriggered() -> None:
    params = dict(_FEATURE_MACHINE)
    params["strips"] = [["S"], ["S"], ["A"]]  # only 2 scatters → trigger unmet
    params["symbols"] = ["S", "A"]
    out = SlotMachine().play({}, SequenceRng([0.0, 0.0, 0.0]), _cfg(params))
    assert out.detail["grid"] == [["S"], ["S"], ["A"]]
    assert "features" not in out.detail
    assert out.multiplier == 0.0


def test_machine_play_deterministic_with_features() -> None:
    cfg = _cfg(_FEATURE_MACHINE)
    a = SlotMachine().play({}, create_rng(b"m-seed", "client", 9), cfg)
    b = SlotMachine().play({}, create_rng(b"m-seed", "client", 9), cfg)
    assert a == b
