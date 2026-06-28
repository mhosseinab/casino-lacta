"""S24 — slots ``machine02`` (LOW volatility) RTP-as-CI gate (spec §B.1, §458, §465).

machine02 is "config + art, not code": a new ``machine02.json`` under
``engine/src/engine/slots/machines/`` + a registry entry + a seed migration — ZERO
engine framework changes. This file is its automatic per-machine RTP gate, mirroring
machine01's gate but driven by the SHARED, parity-locked harness (:mod:`slots_sim`).

**Low-volatility design:** frequent short-run wins (2-of-a-kind on common symbols),
compressed top multipliers, a gentle freeSpins feature — so per-spin payout variance
is LOW while RTP stays at the JSON target. The volatility CONTRAST against the
high-volatility machine03 is asserted in ``test_slots_machine03_rtp.py``.

**The soundness chain (same as machine01):** ONE config-driven evaluator
(:func:`slots_sim.simulate`), driven from the SAME ``cfg.params`` the engine reads, is
proven EXACTLY equal to ``SlotMachine.play`` (grid + multiplier) on a shared uniform
feed — base, FORCED feature, FORCED retrigger, and the all-wild fallback. With parity
proven, ``sim ≈ target ⟹ engine ≈ target``: tuning the parity-locked sim IS tuning the
engine. Tolerance is the variance-aware ``z·√(Var/n)`` (5σ), NEVER hand-widened.

``numpy`` is used in ``tests/`` ONLY (never ``engine/``). RTP/parity are DB-FREE.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

import slots_sim
from app.slots import load_machine
from engine.rng import create_rng
from engine.slots.framework import SlotMachine
from engine.types import GameConfig

MACHINE_ID = "slots.machine02"
_MACHINE = load_machine(MACHINE_ID)
PARAMS: dict[str, Any] = _MACHINE.params
TARGET_RTP: float = _MACHINE.rtp  # 0.96, from the JSON (single source of truth)

_GAME = SlotMachine()
_CFG = GameConfig(edge=_MACHINE.edge, params=PARAMS)
_Z = slots_sim.Z


# ---------------------------------------------------------------- 1. PARITY (the gate's meaning)


def test_numpy_sim_matches_engine_exactly() -> None:
    """``simulate`` == ``SlotMachine.play`` EXACTLY on a shared uniform feed, with the
    feature + retrigger + all-wild branches forced and guarded by min-count assertions."""
    triggered, retrigger, all_wild = slots_sim.assert_parity(
        PARAMS, _GAME, _CFG, seed=20240602, min_trigger=1200, min_retrigger=300
    )
    print(
        f"\n[machine02 parity] triggered={triggered} retrigger={retrigger} "
        f"all_wild={all_wild}"
    )


# ---------------------------------------------------------------- 2. RTP sim (numpy, vectorized)


def test_machine02_rtp_pr() -> None:
    """1e7 spins on PR: measured RTP within the 5σ CLT half-width of the JSON target."""
    slots_sim.assert_rtp(PARAMS, TARGET_RTP, MACHINE_ID, 10_000_000, seed=602001, label="1e7/PR")


@pytest.mark.rtp_heavy
def test_machine02_rtp_heavy() -> None:
    """1e8 spins (CI rtp-gate / nightly): the tighter half-width still holds."""
    slots_sim.assert_rtp(
        PARAMS, TARGET_RTP, MACHINE_ID, 100_000_000, seed=602002, label="1e8/nightly"
    )


# ---------------------------------------------------------------- 3. analytic RTP (exact, fast)


def test_analytic_rtp_matches_target() -> None:
    """The exact analytic RTP equals the JSON target (deterministic, every PR)."""
    rtp = slots_sim.analytic_rtp(PARAMS)
    assert abs(rtp - TARGET_RTP) <= 0.002, (
        f"analytic RTP {rtp:.6f} deviates {abs(rtp - TARGET_RTP):.6f} > 0.002 "
        f"from target {TARGET_RTP:.6f} — tune strips/paytable, never the tolerance"
    )
    print(f"\n[machine02 analytic] rtp={rtp:.6f} target={TARGET_RTP:.6f}")


# ---------------------------------------------------------------- 4. reel-strip frequency


def test_reel_strip_frequency() -> None:
    """Empirically drawn grid cells match the strip symbol frequencies (5σ binomial)."""
    n = 200_000
    reels, rows = PARAMS["reels"], PARAMS["rows"]
    cells = n * reels * rows
    counts: dict[str, int] = {}
    for nonce in range(n):
        grid = _GAME.play({}, create_rng(b"machine02-freq", "freq", nonce), _CFG).detail["grid"]
        for col in grid:
            for cell in col:
                counts[cell] = counts.get(cell, 0) + 1
    strip = PARAMS["strips"][0]
    length = len(strip)
    for symbol in PARAMS["symbols"]:
        expected = strip.count(symbol) / length
        observed = counts.get(symbol, 0) / cells
        half_width = _Z * math.sqrt(max(expected * (1 - expected), 1e-12) / cells)
        assert abs(observed - expected) <= half_width, (
            f"symbol {symbol}: observed {observed:.5f} vs strip freq {expected:.5f} "
            f"(dev {abs(observed - expected):.5f} > 5σ {half_width:.5f})"
        )


# ---------------------------------------------------------------- 5. feature-trigger rate


def test_feature_trigger_rate() -> None:
    """Observed feature-trigger frequency ≈ the config-implied rate (5σ binomial)."""
    expected = slots_sim.trigger_rate(PARAMS)
    n = 500_000
    triggered = 0
    for nonce in range(n):
        if _GAME.play({}, create_rng(b"machine02-trig", "trig", nonce), _CFG).detail.get(
            "features"
        ):
            triggered += 1
    observed = triggered / n
    half_width = _Z * math.sqrt(max(expected * (1 - expected), 1e-12) / n)
    assert abs(observed - expected) <= half_width, (
        f"trigger rate observed {observed:.6f} vs implied {expected:.6f} "
        f"(dev {abs(observed - expected):.6f} > 5σ {half_width:.6f}, n={n})"
    )
    print(f"\n[machine02 trigger] observed={observed:.6f} implied={expected:.6f} n={n}")


# ---------------------------------------------------------------- 6. paytable correctness


def test_paytable_evaluation_correctness() -> None:
    """A hand-placed full centre line pays the configured paytable multiplier (÷num_lines)."""
    reels = PARAMS["reels"]
    strip = PARAMS["strips"][0]
    length = len(strip)
    n_lines = len(PARAMS["paylines"])
    # Force the centre row (payline 0 = [1,1,1,1,1]) to a full line of M by landing a
    # stop whose row-1 cell is M on every reel.
    m_stop = next(s for s in range(length) if strip[(s + 1) % length] == "M")
    out = _GAME.play(
        {}, slots_sim.ReplayRng([slots_sim.u_for_stop(m_stop, length)] * reels + [0.0] * 60), _CFG
    )
    centre = [col[1] for col in out.detail["grid"]]
    assert centre == ["M"] * reels, centre
    m5 = PARAMS["paytable"]["M"]["5"]
    line0 = next(w for w in out.detail["lineWins"] if w["line"] == 0)
    assert line0 == {"line": 0, "symbol": "M", "count": 5, "multiplier": m5}
    assert out.multiplier >= m5 / n_lines


def test_determinism_same_seed_same_outcome() -> None:
    """A spin is a pure function of (serverSeed, clientSeed, nonce)."""
    a = _GAME.play({}, create_rng(b"m02-seed", "client", 11), _CFG)
    b = _GAME.play({}, create_rng(b"m02-seed", "client", 11), _CFG)
    assert a == b
