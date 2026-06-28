"""Shared slots RTP/parity harness for the config-driven machines (S23/S24).

The per-machine gates (``tests/test_slots_machine0{2,3}_rtp.py``) reuse ONE vectorized
evaluator (:func:`simulate`) and ONE exact analytic (:func:`analytic_rtp`) from here,
parameterized by the machine's ``GameConfig.params`` — the SAME data the engine reads.
This is test code (numpy lives in ``tests/`` only, never ``engine/``); it mirrors
``SlotMachine.play`` so the parity test (:func:`assert_parity`) can prove
``simulate == engine`` EXACTLY on a shared uniform feed, making "tune the strips"
tune the engine, not a drifting sim.

Scope (matches both S24 machines + S23 ``machine01``): **lines** mode, identical
strips across reels, a single retrigger-capable **freeSpins** feature, scatter is
trigger-only (no ``scatterPaytable``). A machine outside that shape would need this
helper extended (or its own sim) — the exact-equality parity test is the safety net
that makes any drift fail loudly.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from typing import Any

import numpy as np

from engine.slots.framework import SlotMachine, _eval_lines
from engine.types import GameConfig

Z = 5.0  # 5σ → ~6e-7 two-sided false-reject; deterministic under the fixed seed.


class ReplayRng:
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


def sym_ids(params: dict[str, Any]) -> dict[str, int]:
    return {s: i for i, s in enumerate(params["symbols"])}


def simulate(
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
    number. Reads per-reel strips, so distinct strips are supported (the S24 machines
    keep them identical, which the analytic/frequency checks rely on)."""
    sym = sym_ids(params)
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

    # feature: a single freeSpins module; trigger = scatter count anywhere.
    feat = params["features"][0]
    assert feat["type"] == "freeSpins", "the slots sim covers the freeSpins feature only"
    trig_sym = sym[feat["trigger"]["symbol"]]
    trig_count = feat["trigger"]["count"]
    prize = feat["prizeStrip"]
    pmult = np.array([p["multiplier"] for p in prize])
    pretr = np.array([p.get("retrigger", 0) for p in prize])
    n_prize = len(prize)
    max_spins = feat["maxSpins"]

    # The engine sums the feature contribution SEPARATELY (apply_features) and adds it to
    # the line part with a SINGLE addition (``total += feature_total``). Mirror that
    # associativity exactly — folding each prize draw into the running multiplier instead
    # would round differently and break exact parity.
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


# ---------------------------------------------------------------- parity helpers


def stops_landing_symbol(strip: list[str], rows: int, symbol: str) -> list[int]:
    """Stops whose ``rows``-tall window over the cyclic strip contains ``symbol``."""
    length = len(strip)
    return [
        stop
        for stop in range(length)
        if any(strip[(stop + r) % length] == symbol for r in range(rows))
    ]


def u_for_stop(stop: int, length: int) -> float:
    """A uniform that floors to ``stop`` (mid-cell, away from boundaries)."""
    return (stop + 0.5) / length


def assert_parity(
    params: dict[str, Any],
    game: SlotMachine,
    cfg: GameConfig,
    seed: int,
    min_trigger: int,
    min_retrigger: int,
) -> tuple[int, int, int]:
    """``simulate`` == ``SlotMachine.play`` EXACTLY (grid + multiplier) on a shared
    uniform feed — base spins, FORCED feature spins, FORCED retrigger spins, and the
    all-wild line fallback. Asserts the feature/retrigger/all-wild coverage cannot
    silently vanish (per-machine min counts). Returns the observed counts."""
    reels, rows = params["reels"], params["rows"]
    strip0 = params["strips"][0]
    length = len(strip0)
    scatter = params["scatter"]
    wild = params["wild"]
    scatter_stops = stops_landing_symbol(strip0, rows, scatter)
    feat = params["features"][0]
    prize = feat["prizeStrip"]
    n_prize = len(prize)
    retrig_idx = next(i for i, p in enumerate(prize) if p.get("retrigger", 0) > 0)
    max_feat_draws = feat["maxSpins"]

    gen = np.random.default_rng(seed)
    reel_rows: list[list[float]] = []
    feat_rows: list[list[float]] = []
    kinds: list[str] = []

    def add(reel: list[float], feat_u: list[float], kind: str) -> None:
        reel_rows.append(reel)
        feat_rows.append(feat_u)
        kinds.append(kind)

    # base spins (random) — most will not trigger
    for _ in range(2000):
        add(gen.random(reels).tolist(), gen.random(max_feat_draws).tolist(), "base")

    # forced-trigger spins: force exactly `forced` reels to show a scatter
    trig_count = feat["trigger"]["count"]
    for forced in (trig_count, trig_count + 1, reels):
        for _ in range(400):
            ru = gen.random(reels).tolist()
            for r in range(forced):
                ru[r] = u_for_stop(scatter_stops[r % len(scatter_stops)], length)
            add(ru, gen.random(max_feat_draws).tolist(), f"trigger{forced}")

    # forced ALL-WILD line on payline 0: too rare to appear by chance, so force it,
    # exercising the ``all_wild`` branch of simulate against the engine.
    line0 = params["paylines"][0]
    all_wild_u = [
        u_for_stop(
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
        for r in range(trig_count):
            ru[r] = u_for_stop(scatter_stops[r % len(scatter_stops)], length)
        fu = gen.random(max_feat_draws).tolist()
        for j in range(3):
            fu[j] = (retrig_idx + 0.5) / n_prize  # forces a retrigger
        add(ru, fu, "retrigger")

    reel_u = np.array(reel_rows)
    feat_u = np.array(feat_rows)
    grid_sim, mult_sim = simulate(params, reel_u, lambda k, b: feat_u[:, k])

    rev = {i: s for s, i in sym_ids(params).items()}
    triggered_seen = retrigger_seen = all_wild_seen = 0
    for i, (ru, fu) in enumerate(zip(reel_rows, feat_rows, strict=True)):
        out = game.play({}, ReplayRng(list(ru) + list(fu)), cfg)
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
        if any(w["symbol"] == wild and w["count"] == reels for w in out.detail["lineWins"]):
            all_wild_seen += 1

    # The parity sample MUST actually exercise the feature (incl. retrigger) AND the
    # all-wild line fallback — a base-only check is blind to exactly the math that matters.
    assert triggered_seen >= min_trigger, f"too few feature spins in parity: {triggered_seen}"
    assert retrigger_seen >= min_retrigger, f"too few retrigger spins in parity: {retrigger_seen}"
    assert all_wild_seen >= 1, "the all-wild line fallback was never exercised in parity"
    return triggered_seen, retrigger_seen, all_wild_seen


# ---------------------------------------------------------------- RTP sim


def measure_rtp(params: dict[str, Any], n: int, batch: int, seed: int) -> tuple[float, float]:
    """Mean + population variance of the per-spin multiplier over ``n`` vectorized spins,
    accumulated in fixed-memory batches (so 1e8 is feasible). Fixed seed → reproducible."""
    gen = np.random.default_rng(seed)
    reels = params["reels"]
    total = 0.0
    total_sq = 0.0
    done = 0
    while done < n:
        b = min(batch, n - done)
        reel_u = gen.random((b, reels))
        _, mult = simulate(params, reel_u, lambda k, b: gen.random(b))
        total += float(mult.sum())
        total_sq += float((mult * mult).sum())
        done += b
    mean = total / n
    variance = max(total_sq / n - mean * mean, 0.0)
    return mean, variance


def assert_rtp(
    params: dict[str, Any], target: float, machine_id: str, n: int, seed: int, label: str
) -> None:
    mean, variance = measure_rtp(params, n, 1_000_000, seed)
    half_width = Z * math.sqrt(variance / n)
    assert abs(mean - target) <= half_width, (
        f"RTP {mean:.6f} for {machine_id} deviates from target {target:.6f} by "
        f"{abs(mean - target):.6f} > tol {half_width:.6f} (n={n}, var={variance:.4f})"
    )
    print(
        f"\n[{machine_id} RTP {label}] rtp={mean:.6f} target={target:.6f} "
        f"dev={abs(mean - target):.6f} tol(5σ CLT)={half_width:.6f} var={variance:.4f} n={n}"
    )


# ---------------------------------------------------------------- exact analytic RTP


def analytic_rtp(params: dict[str, Any]) -> float:
    """Exact RTP = base + feature, computed independently of the sim.

    Base (lines mode, identical strips): each line cell's marginal is the strip symbol
    frequency, so ``E[total] = E[single line]`` (the ÷num_lines cancels). Enumerate the
    symbol combinations, weight by the product of per-reel frequencies, and evaluate via
    the ENGINE's ``_eval_lines`` (no re-implementation → no drift). Feature:
    ``P(trigger) · m · spins / (1 - r)`` (Wald) where ``m``/``r`` are the prize-strip mean
    multiplier / retrigger and ``P(trigger)`` convolves the per-reel window scatter-count
    distribution."""
    strip = params["strips"][0]
    length = len(strip)
    symbols = params["symbols"]
    reels, rows = params["reels"], params["rows"]
    freq = {s: strip.count(s) / length for s in symbols}

    base = 0.0
    for combo in itertools.product(symbols, repeat=reels):
        prob = 1.0
        for s in combo:
            prob *= freq[s]
        if prob == 0.0:
            continue
        line_sum, _ = _eval_lines(
            [[c] for c in combo], [[0] * reels], params["paytable"], params["wild"]
        )
        base += prob * line_sum

    scatter = params["scatter"]
    window = np.zeros(rows + 1)
    for stop in range(length):
        k = sum(1 for r in range(rows) if strip[(stop + r) % length] == scatter)
        window[k] += 1.0 / length
    dist = np.array([1.0])
    for _ in range(reels):
        dist = np.convolve(dist, window)
    feat = params["features"][0]
    p_trigger = float(dist[feat["trigger"]["count"] :].sum())
    prize = feat["prizeStrip"]
    m = float(np.mean([p["multiplier"] for p in prize]))
    r = float(np.mean([p.get("retrigger", 0) for p in prize]))
    feature = p_trigger * m * feat["spins"] / (1.0 - r)
    return base + feature


def trigger_rate(params: dict[str, Any]) -> float:
    """Config-implied feature-trigger probability (convolved window scatter-count dist)."""
    strip = params["strips"][0]
    length = len(strip)
    rows, reels = params["rows"], params["reels"]
    scatter = params["scatter"]
    window = np.zeros(rows + 1)
    for stop in range(length):
        k = sum(1 for r in range(rows) if strip[(stop + r) % length] == scatter)
        window[k] += 1.0 / length
    dist = np.array([1.0])
    for _ in range(reels):
        dist = np.convolve(dist, window)
    feat = params["features"][0]
    return float(dist[feat["trigger"]["count"] :].sum())
