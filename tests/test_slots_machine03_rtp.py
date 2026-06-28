"""S24 — slots ``machine03`` (HIGH volatility) RTP-as-CI gate (spec §B.1, §458, §465).

machine03 is "config + art, not code": ``machine03.json`` + a registry entry + a seed
migration, ZERO engine framework changes. Its automatic per-machine RTP gate mirrors
machine01/02 via the SHARED, parity-locked harness (:mod:`slots_sim`).

**High-volatility design:** wins only on long runs (4/5-of-a-kind) of rarer symbols,
large top multipliers, and a punchy freeSpins feature whose prize strip is mostly zero
with rare huge entries + a retrigger — so per-spin payout variance is HIGH while RTP
stays at the JSON target. :func:`test_volatility_contrast_low_vs_high` asserts the
machine03 per-spin variance is dramatically greater than machine02's (the whole point
of shipping distinct volatility profiles).

Same soundness chain + iron rule as machine01/02: parity-locked sim, exact analytic
cross-check, variance-aware 5σ ``z·√(Var/n)`` tolerance NEVER hand-widened. ``numpy`` in
``tests/`` ONLY; RTP/parity are DB-FREE.
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

MACHINE_ID = "slots.machine03"
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
        PARAMS, _GAME, _CFG, seed=20240603, min_trigger=1200, min_retrigger=300
    )
    print(
        f"\n[machine03 parity] triggered={triggered} retrigger={retrigger} "
        f"all_wild={all_wild}"
    )


# ---------------------------------------------------------------- 2. RTP sim (numpy, vectorized)


def test_machine03_rtp_pr() -> None:
    """1e7 spins on PR: measured RTP within the 5σ CLT half-width of the JSON target."""
    slots_sim.assert_rtp(PARAMS, TARGET_RTP, MACHINE_ID, 10_000_000, seed=603001, label="1e7/PR")


@pytest.mark.rtp_heavy
def test_machine03_rtp_heavy() -> None:
    """1e8 spins (CI rtp-gate / nightly): the tighter half-width still holds."""
    slots_sim.assert_rtp(
        PARAMS, TARGET_RTP, MACHINE_ID, 100_000_000, seed=603002, label="1e8/nightly"
    )


# ---------------------------------------------------------------- 3. analytic RTP (exact, fast)


def test_analytic_rtp_matches_target() -> None:
    """The exact analytic RTP equals the JSON target (deterministic, every PR)."""
    rtp = slots_sim.analytic_rtp(PARAMS)
    assert abs(rtp - TARGET_RTP) <= 0.002, (
        f"analytic RTP {rtp:.6f} deviates {abs(rtp - TARGET_RTP):.6f} > 0.002 "
        f"from target {TARGET_RTP:.6f} — tune strips/paytable, never the tolerance"
    )
    print(f"\n[machine03 analytic] rtp={rtp:.6f} target={TARGET_RTP:.6f}")


# ---------------------------------------------------------------- 4. reel-strip frequency


def test_reel_strip_frequency() -> None:
    """Empirically drawn grid cells match the strip symbol frequencies (5σ binomial)."""
    n = 200_000
    reels, rows = PARAMS["reels"], PARAMS["rows"]
    cells = n * reels * rows
    counts: dict[str, int] = {}
    for nonce in range(n):
        grid = _GAME.play({}, create_rng(b"machine03-freq", "freq", nonce), _CFG).detail["grid"]
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
        if _GAME.play({}, create_rng(b"machine03-trig", "trig", nonce), _CFG).detail.get(
            "features"
        ):
            triggered += 1
    observed = triggered / n
    half_width = _Z * math.sqrt(max(expected * (1 - expected), 1e-12) / n)
    assert abs(observed - expected) <= half_width, (
        f"trigger rate observed {observed:.6f} vs implied {expected:.6f} "
        f"(dev {abs(observed - expected):.6f} > 5σ {half_width:.6f}, n={n})"
    )
    print(f"\n[machine03 trigger] observed={observed:.6f} implied={expected:.6f} n={n}")


# ---------------------------------------------------------------- 6. paytable correctness


def test_paytable_evaluation_correctness() -> None:
    """A hand-placed full centre line pays the configured paytable multiplier (÷num_lines)."""
    reels = PARAMS["reels"]
    strip = PARAMS["strips"][0]
    length = len(strip)
    n_lines = len(PARAMS["paylines"])
    h_stop = next(s for s in range(length) if strip[(s + 1) % length] == "H")
    out = _GAME.play(
        {}, slots_sim.ReplayRng([slots_sim.u_for_stop(h_stop, length)] * reels + [0.0] * 80), _CFG
    )
    centre = [col[1] for col in out.detail["grid"]]
    assert centre == ["H"] * reels, centre
    h5 = PARAMS["paytable"]["H"]["5"]
    line0 = next(w for w in out.detail["lineWins"] if w["line"] == 0)
    assert line0 == {"line": 0, "symbol": "H", "count": 5, "multiplier": h5}
    assert out.multiplier >= h5 / n_lines


def test_determinism_same_seed_same_outcome() -> None:
    """A spin is a pure function of (serverSeed, clientSeed, nonce)."""
    a = _GAME.play({}, create_rng(b"m03-seed", "client", 11), _CFG)
    b = _GAME.play({}, create_rng(b"m03-seed", "client", 11), _CFG)
    assert a == b


# ---------------------------------------------------------------- 7. volatility contrast


def test_volatility_contrast_low_vs_high() -> None:
    """machine03 (high vol) has dramatically greater per-spin payout variance than
    machine02 (low vol) — the whole point of distinct volatility profiles. Both are
    tuned to the SAME RTP (0.96); only the variance differs. Measured on a shared 1e6
    vectorized sample via the parity-locked harness."""
    n = 1_000_000
    low = load_machine("slots.machine02").params
    high = PARAMS
    mean_low, var_low = slots_sim.measure_rtp(low, n, 1_000_000, seed=900002)
    mean_high, var_high = slots_sim.measure_rtp(high, n, 1_000_000, seed=900003)
    print(
        f"\n[volatility contrast] machine02(low) mean={mean_low:.4f} var={var_low:.4f} | "
        f"machine03(high) mean={mean_high:.4f} var={var_high:.4f} | "
        f"ratio={var_high / var_low:.2f}x"
    )
    assert var_high > 3.0 * var_low, (
        f"high-vol variance {var_high:.3f} not >3x low-vol {var_low:.3f} "
        f"(ratio {var_high / var_low:.2f}x) — volatility profiles are not distinct"
    )
