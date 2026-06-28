"""S23 — slots ``machine01`` RTP-as-CI gate (spec §B.1, §458, §465).

A complete machine (config + art placeholders) ships as ``machine01.json`` under
``engine/src/engine/slots/machines/`` (INERT DATA — engine purity forbids reading it;
it is loaded app-side via :func:`app.slots.load_machine`). This file is the gate that
proves the machine's RTP, automatically, per machine.

**The soundness chain — why this gate is not theater.** A vectorized ``numpy`` sim
that DRIFTS from the engine would let "tune the strips" tune the SIM while the engine
pays something else. So there is ONE config-driven evaluator (:func:`_simulate`),
driven from the SAME ``cfg.params`` the engine reads, and the **parity test**
(:func:`test_numpy_sim_matches_engine_exactly`) proves ``_simulate`` == the engine
``SlotMachine.play`` EXACTLY (grid + multiplier) on a shared uniform feed — INCLUDING
forced feature-triggered + retrigger spins (the variable-draw path where vectorization
most easily diverges). Parity is NOT "same seed" (the engine consumes HMAC floats,
numpy its own PRNG) — it is **same uniform feed**: a batch of uniforms is drawn once,
replayed into the engine via :class:`_ReplayRng` (``.next()`` pops the next pre-drawn
uniform, in the engine's draw order) and fed to ``_simulate`` column-wise. With parity
proven, ``sim ≈ target ⟹ engine ≈ target`` — so tuning the parity-locked sim IS tuning
the engine.

**Measurements (spec §465):** parity (engine==numpy), RTP within a CLT half-width of
the JSON target over **1e7 on PR** / **1e8 nightly** (``rtp_heavy`` → the CI rtp-gate),
an exact deterministic analytic RTP cross-check, a reel-strip frequency check, a
feature-trigger-rate check, paytable-evaluation correctness, and determinism.

**The iron rule:** RTP is tuned via strip weights / paytable, NEVER by clamping an
outcome; the tolerance is the variance-aware ``z·√(Var/n)`` (5σ) and is NEVER widened
to pass — it only NARROWS as ``n`` grows.

``numpy`` is used in ``tests/`` ONLY (never ``engine/``). The core RTP + parity tests
are DB-FREE (the config is read straight from the JSON).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from app.slots import load_machine
from engine.rng import create_rng
from engine.slots.framework import SlotMachine, _eval_lines
from engine.types import GameConfig

MACHINE_ID = "slots.machine01"
_MACHINE = load_machine(MACHINE_ID)
PARAMS: dict[str, Any] = _MACHINE.params
TARGET_RTP: float = _MACHINE.rtp  # 0.96, from the JSON (single source of truth)

_Z = 5.0  # 5σ → ~6e-7 two-sided false-reject; deterministic under the fixed seed.
_GAME = SlotMachine()
_CFG = GameConfig(edge=_MACHINE.edge, params=PARAMS)


# ---------------------------------------------------------------- the shared evaluator


class _ReplayRng:
    """An ``RngStream`` stub: ``next()`` pops the next pre-drawn uniform (engine draw
    order). Lets a test feed the engine the SAME uniforms the numpy path consumes, so
    parity is a statement about identical entropy — not a shared seed."""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._i = 0

    def next(self) -> float:
        value = self._values[self._i]
        self._i += 1
        return value


def _sym_ids(params: dict[str, Any]) -> dict[str, int]:
    return {s: i for i, s in enumerate(params["symbols"])}


def _simulate(
    params: dict[str, Any],
    reel_u: np.ndarray,
    feat_uniform: Callable[[int, int], np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized per-spin evaluation mirroring ``SlotMachine.play`` draw order EXACTLY.

    ``reel_u`` is ``(b, reels)`` uniforms (one stop draw per reel); ``feat_uniform(k, b)``
    yields the ``(b,)`` uniforms for feature round ``k`` (free-spins draws one prize per
    played spin). Returns ``(grid_ids (b, reels, rows), multiplier (b,))``. The SAME
    function powers the RTP sim (uniforms from a numpy Generator) and the parity test
    (uniforms from a fixed feed) — so the number the gate measures is the parity-proven
    number.
    """
    sym = _sym_ids(params)
    reels, rows = params["reels"], params["rows"]
    strips = [np.array([sym[s] for s in params["strips"][r]]) for r in range(reels)]
    wild = sym[params["wild"]] if params.get("wild") is not None else -1
    paylines = params["paylines"]
    b = reel_u.shape[0]

    pay = np.zeros((len(sym), reels + 1))
    for s, row in params["paytable"].items():
        for c, m in row.items():
            pay[sym[s], int(c)] = m

    # reels → stops → grid (b, reels, rows) over the cyclic per-reel strip
    grid = np.empty((b, reels, rows), dtype=np.int64)
    for r in range(reels):
        length = len(strips[r])
        stops = np.floor(reel_u[:, r] * length).astype(np.int64)
        for row in range(rows):
            grid[:, r, row] = strips[r][(stops + row) % length]

    # lines: accumulate per-line multipliers IN PAYLINE ORDER (matches the engine's
    # sequential sum exactly), then divide by the line count.
    line_sum = np.zeros(b)
    for line in paylines:
        syms = np.stack([grid[:, r, line[r]] for r in range(reels)], axis=1)
        is_wild = syms == wild
        all_wild = is_wild.all(axis=1)
        first_nonwild = (~is_wild).argmax(axis=1)
        paying = np.where(all_wild, wild, syms[np.arange(b), first_nonwild])
        match = (syms == paying[:, None]) | is_wild
        count = np.cumprod(match, axis=1).sum(axis=1)  # leading run length
        line_sum += pay[paying, count]
    line_part = line_sum / len(paylines)

    # feature (machine01: a single freeSpins module; trigger = scatter count anywhere).
    feat = params["features"][0]
    assert feat["type"] == "freeSpins", "the machine01 sim covers its freeSpins feature"
    trig_sym = sym[feat["trigger"]["symbol"]]
    trig_count = feat["trigger"]["count"]
    prize = feat["prizeStrip"]
    pmult = np.array([p["multiplier"] for p in prize])
    pretr = np.array([p.get("retrigger", 0) for p in prize])
    n_prize = len(prize)
    max_spins = feat["maxSpins"]

    # The engine sums the feature contribution SEPARATELY (in apply_features) and adds
    # it to the line part with a SINGLE addition (``total += feature_total``). Mirror
    # that associativity exactly — folding each prize draw into the running multiplier
    # instead would round differently and break exact parity.
    triggered = (grid == trig_sym).sum(axis=(1, 2)) >= trig_count
    feat_total = np.zeros(b)
    remaining = np.where(triggered, feat["spins"], 0).astype(np.int64)
    k = 0
    while remaining.max() > 0 and k < max_spins:
        active = remaining > 0
        idx = np.floor(feat_uniform(k, b) * n_prize).astype(np.int64)
        feat_total = feat_total + np.where(active, pmult[idx], 0.0)
        remaining = np.where(active, remaining + pretr[idx] - 1, remaining)
        k += 1
    multiplier = line_part + feat_total
    return grid, multiplier


# ---------------------------------------------------------------- 1. PARITY (the gate's meaning)


def _stops_landing_symbol(strip: list[str], rows: int, symbol: str) -> list[int]:
    """Stops whose ``rows``-tall window over the cyclic strip contains ``symbol``."""
    length = len(strip)
    return [
        stop
        for stop in range(length)
        if any(strip[(stop + r) % length] == symbol for r in range(rows))
    ]


def _u_for_stop(stop: int, length: int) -> float:
    """A uniform that floors to ``stop`` (mid-cell, away from boundaries)."""
    return (stop + 0.5) / length


def test_numpy_sim_matches_engine_exactly() -> None:
    """``_simulate`` == ``SlotMachine.play`` EXACTLY (grid + multiplier) on a shared
    uniform feed — base spins, FORCED feature spins, and FORCED retrigger spins. This
    is what makes the RTP sim a faithful proxy for the engine (no drift)."""
    reels, rows = PARAMS["reels"], PARAMS["rows"]
    strip0 = PARAMS["strips"][0]
    length = len(strip0)
    scatter = PARAMS["scatter"]
    scatter_stops = _stops_landing_symbol(strip0, rows, scatter)
    feat = PARAMS["features"][0]
    prize = feat["prizeStrip"]
    n_prize = len(prize)
    retrig_idx = next(i for i, p in enumerate(prize) if p.get("retrigger", 0) > 0)
    max_feat_draws = feat["maxSpins"]

    gen = np.random.default_rng(20230523)
    reel_rows: list[list[float]] = []
    feat_rows: list[list[float]] = []
    kinds: list[str] = []

    def add(reel_u: list[float], feat_u: list[float], kind: str) -> None:
        reel_rows.append(reel_u)
        feat_rows.append(feat_u)
        kinds.append(kind)

    # base spins (random) — most will not trigger
    for _ in range(2000):
        add(gen.random(reels).tolist(), gen.random(max_feat_draws).tolist(), "base")

    # forced-trigger spins: force exactly `forced` reels to show a scatter
    for forced in (3, 4, 5):
        for _ in range(400):
            ru = gen.random(reels).tolist()
            for r in range(forced):
                ru[r] = _u_for_stop(scatter_stops[r % len(scatter_stops)], length)
            add(ru, gen.random(max_feat_draws).tolist(), f"trigger{forced}")

    # forced ALL-WILD line: the all-wild line-eval fallback (paying → wild) is too rare
    # to appear by chance, so force it — land, on every reel, a stop whose payline-0
    # cell is the wild, exercising the ``all_wild`` branch of _simulate against the engine.
    wild = PARAMS["wild"]
    line0 = PARAMS["paylines"][0]
    all_wild_u = [
        _u_for_stop(
            next(s for s in range(length) if strip0[(s + line0[r]) % length] == wild), length
        )
        for r in range(reels)
    ]
    for _ in range(50):
        add(list(all_wild_u), gen.random(max_feat_draws).tolist(), "allwild")

    # forced-RETRIGGER spins: trigger, and make the first feature draws select the
    # retrigger prize so the variable-draw retrigger loop is genuinely exercised.
    for _ in range(400):
        ru = gen.random(reels).tolist()
        for r in range(3):
            ru[r] = _u_for_stop(scatter_stops[r % len(scatter_stops)], length)
        fu = gen.random(max_feat_draws).tolist()
        for j in range(3):
            fu[j] = (retrig_idx + 0.5) / n_prize  # forces a retrigger
        add(ru, fu, "retrigger")

    reel_u = np.array(reel_rows)
    feat_u = np.array(feat_rows)

    grid_sim, mult_sim = _simulate(PARAMS, reel_u, lambda k, b: feat_u[:, k])

    sym = _sym_ids(PARAMS)
    rev = {i: s for s, i in sym.items()}
    triggered_seen = 0
    retrigger_seen = 0
    all_wild_seen = 0
    wild = PARAMS["wild"]
    reels_n = PARAMS["reels"]
    for i, (ru, fu) in enumerate(zip(reel_rows, feat_rows, strict=True)):
        out = _GAME.play({}, _ReplayRng(list(ru) + list(fu)), _CFG)
        eng_grid = out.detail["grid"]
        sim_grid = [[rev[int(grid_sim[i, r, row])] for row in range(rows)] for r in range(reels)]
        assert eng_grid == sim_grid, f"grid mismatch row {i} ({kinds[i]})"
        assert out.multiplier == float(mult_sim[i]), (
            f"multiplier mismatch row {i} ({kinds[i]}): "
            f"engine {out.multiplier!r} != sim {float(mult_sim[i])!r}"
        )
        features = out.detail.get("features")
        if features:
            triggered_seen += 1
            if features[0]["spinsPlayed"] > feat["spins"]:
                retrigger_seen += 1
        if any(w["symbol"] == wild and w["count"] == reels_n for w in out.detail["lineWins"]):
            all_wild_seen += 1

    # The parity sample MUST actually exercise the feature (incl. retrigger) AND the
    # all-wild line fallback — a base-only check is blind to exactly the math that matters.
    assert triggered_seen >= 1200, f"too few feature spins in parity sample: {triggered_seen}"
    assert retrigger_seen >= 300, f"too few retrigger spins in parity sample: {retrigger_seen}"
    assert all_wild_seen >= 1, "the all-wild line fallback was never exercised in parity"


# ---------------------------------------------------------------- 2. RTP sim (numpy, vectorized)


def _measure_rtp(n: int, batch: int, seed: int) -> tuple[float, float]:
    """Mean + population variance of the per-spin multiplier over ``n`` vectorized spins,
    accumulated in fixed-memory batches (so 1e8 is feasible). Fixed seed → reproducible."""
    gen = np.random.default_rng(seed)
    reels = PARAMS["reels"]
    total = 0.0
    total_sq = 0.0
    done = 0
    while done < n:
        b = min(batch, n - done)
        reel_u = gen.random((b, reels))
        _, mult = _simulate(PARAMS, reel_u, lambda k, b: gen.random(b))
        total += float(mult.sum())
        total_sq += float((mult * mult).sum())
        done += b
    mean = total / n
    variance = max(total_sq / n - mean * mean, 0.0)
    return mean, variance


def _assert_rtp(n: int, seed: int, label: str) -> None:
    mean, variance = _measure_rtp(n, 1_000_000, seed)
    half_width = _Z * math.sqrt(variance / n)
    assert abs(mean - TARGET_RTP) <= half_width, (
        f"RTP {mean:.6f} for {MACHINE_ID} deviates from target {TARGET_RTP:.6f} by "
        f"{abs(mean - TARGET_RTP):.6f} > tol {half_width:.6f} (n={n}, var={variance:.4f})"
    )
    print(
        f"\n[machine01 RTP {label}] rtp={mean:.6f} target={TARGET_RTP:.6f} "
        f"dev={abs(mean - TARGET_RTP):.6f} tol(5σ CLT)={half_width:.6f} "
        f"var={variance:.4f} n={n}"
    )


def test_machine01_rtp_pr() -> None:
    """1e7 spins on PR: measured RTP within the 5σ CLT half-width of the JSON target."""
    _assert_rtp(10_000_000, seed=523001, label="1e7/PR")


@pytest.mark.rtp_heavy
def test_machine01_rtp_heavy() -> None:
    """1e8 spins (CI rtp-gate / nightly): the tighter half-width still holds."""
    _assert_rtp(100_000_000, seed=523002, label="1e8/nightly")


# ---------------------------------------------------------------- 3. analytic RTP (exact, fast)


def _analytic_rtp() -> float:
    """Exact RTP = base + feature, computed independently of the sim.

    Base (lines mode): with independent reels and the stop uniform, each line cell's
    marginal is the strip symbol frequency, so ``E[total] = E[single line]`` (the
    ÷num_lines cancels). Enumerate the 6^reels symbol combinations, weight by the
    product of per-reel frequencies, and evaluate via the ENGINE's ``_eval_lines`` (no
    re-implementation → no drift). Feature: ``P(trigger) · m · spins / (1 - r)`` where
    ``m``/``r`` are the prize-strip mean multiplier / retrigger and ``P(trigger)`` comes
    from convolving the per-reel window scatter-count distribution.
    """
    strip = PARAMS["strips"][0]
    length = len(strip)
    symbols = PARAMS["symbols"]
    reels, rows = PARAMS["reels"], PARAMS["rows"]
    freq = {s: strip.count(s) / length for s in symbols}

    base = 0.0
    for combo in itertools.product(symbols, repeat=reels):
        prob = 1.0
        for s in combo:
            prob *= freq[s]
        if prob == 0.0:
            continue
        line_sum, _ = _eval_lines(
            [[c] for c in combo], [[0] * reels], PARAMS["paytable"], PARAMS["wild"]
        )
        base += prob * line_sum

    scatter = PARAMS["scatter"]
    window = np.zeros(rows + 1)
    for stop in range(length):
        k = sum(1 for r in range(rows) if strip[(stop + r) % length] == scatter)
        window[k] += 1.0 / length
    dist = np.array([1.0])
    for _ in range(reels):
        dist = np.convolve(dist, window)
    feat = PARAMS["features"][0]
    p_trigger = float(dist[feat["trigger"]["count"] :].sum())
    prize = feat["prizeStrip"]
    m = float(np.mean([p["multiplier"] for p in prize]))
    r = float(np.mean([p.get("retrigger", 0) for p in prize]))
    feature = p_trigger * m * feat["spins"] / (1.0 - r)
    return base + feature


def test_analytic_rtp_matches_target() -> None:
    """The exact analytic RTP equals the JSON target (deterministic, every PR)."""
    rtp = _analytic_rtp()
    assert abs(rtp - TARGET_RTP) <= 0.002, (
        f"analytic RTP {rtp:.6f} deviates {abs(rtp - TARGET_RTP):.6f} > 0.002 "
        f"from target {TARGET_RTP:.6f} — tune strips/paytable, never the tolerance"
    )
    print(f"\n[machine01 analytic] rtp={rtp:.6f} target={TARGET_RTP:.6f}")


# ---------------------------------------------------------------- 4. reel-strip frequency


def test_reel_strip_frequency() -> None:
    """Empirically drawn grid cells match the strip symbol frequencies (5σ binomial) —
    catches a biased stop draw / wrong strip."""
    n = 200_000
    reels, rows = PARAMS["reels"], PARAMS["rows"]
    cells = n * reels * rows
    counts: dict[str, int] = {}
    for nonce in range(n):
        rng = create_rng(b"machine01-freq", "freq", nonce)
        grid = _GAME.play({}, rng, _CFG).detail["grid"]
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
    strip = PARAMS["strips"][0]
    length = len(strip)
    rows, reels = PARAMS["rows"], PARAMS["reels"]
    scatter = PARAMS["scatter"]
    window = np.zeros(rows + 1)
    for stop in range(length):
        k = sum(1 for r in range(rows) if strip[(stop + r) % length] == scatter)
        window[k] += 1.0 / length
    dist = np.array([1.0])
    for _ in range(reels):
        dist = np.convolve(dist, window)
    feat = PARAMS["features"][0]
    expected = float(dist[feat["trigger"]["count"] :].sum())

    n = 500_000
    triggered = 0
    for nonce in range(n):
        rng = create_rng(b"machine01-trig", "trig", nonce)
        if _GAME.play({}, rng, _CFG).detail.get("features"):
            triggered += 1
    observed = triggered / n
    half_width = _Z * math.sqrt(max(expected * (1 - expected), 1e-12) / n)
    assert abs(observed - expected) <= half_width, (
        f"trigger rate observed {observed:.6f} vs implied {expected:.6f} "
        f"(dev {abs(observed - expected):.6f} > 5σ {half_width:.6f}, n={n})"
    )
    print(f"\n[machine01 trigger] observed={observed:.6f} implied={expected:.6f} n={n}")


# ---------------------------------------------------------------- 6. paytable correctness


def _play_forced(reel_u: list[float], feat_u: list[float] | None = None) -> Any:
    return _GAME.play({}, _ReplayRng(list(reel_u) + list(feat_u or [0.0] * 60)), _CFG)


def test_paytable_evaluation_correctness() -> None:
    """Hand-placed grids pay the configured paytable multiplier (÷num_lines)."""
    reels = PARAMS["reels"]
    strip = PARAMS["strips"][0]
    length = len(strip)
    paytable = PARAMS["paytable"]
    n_lines = len(PARAMS["paylines"])

    # Force the centre row (payline 0 = [1,1,1,1,1]) to a full line of H by landing a
    # stop whose row-1 cell is H on every reel. H sits at strip index 25/26.
    h_stop = next(s for s in range(length) if strip[(s + 1) % length] == "H")
    out = _play_forced([_u_for_stop(h_stop, length)] * reels)
    centre = [col[1] for col in out.detail["grid"]]
    assert centre == ["H"] * reels, centre
    # The full-H centre line contributes H×5; other lines read different rows. Assert
    # the centre line win is recorded with the configured multiplier.
    h5 = paytable["H"]["5"]
    line0 = next(w for w in out.detail["lineWins"] if w["line"] == 0)
    assert line0 == {"line": 0, "symbol": "H", "count": 5, "multiplier": h5}
    # And the total includes that line's contribution ÷ num_lines.
    assert out.multiplier >= h5 / n_lines


def test_determinism_same_seed_same_outcome() -> None:
    """A spin is a pure function of (serverSeed, clientSeed, nonce)."""
    a = _GAME.play({}, create_rng(b"m01-seed", "client", 11), _CFG)
    b = _GAME.play({}, create_rng(b"m01-seed", "client", 11), _CFG)
    assert a == b
