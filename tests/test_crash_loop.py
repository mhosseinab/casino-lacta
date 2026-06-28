"""S18 — Crash round CORE: C distribution + instant-bust rate + verifier parity.

This is the engine-level gate (no async loop, no Redis, no WS): it drives the
PRODUCTION C-computation path — ``CrashRound.open(...).C`` via the pure core's
``crash_point_for_round`` — so the test can never drift from what the actor
settles. C is fixed at round start from a committed per-round seed; the cosmetic
tick curve (in the async shell) never enters here.

Two distribution facts (spec §A.4, ``_curve.py``'s warning):

* **Survival** ``P(C >= x) = (1 - edge)/x`` for ``x > 1.00`` (x=2 → 0.495,
  x=10 → 0.099). Each measured against a 5σ binomial CLT half-width
  ``z·sqrt(p(1-p)/n)`` (derived, never a flat band) — it NARROWS as n grows.
* **Instant-bust** ``P(f < edge) = edge`` — measured on the CONTINUOUS draw
  ``f``, NOT ``P(C == 1.00)`` which is the floored mass ≈ 2·edge. At n here the
  5σ band is tight enough that the floored-mass trap (≈0.0198) would FAIL the
  edge (0.01) assertion — so this discriminates the exact bug ``_curve.py`` warns
  about.

Parity ties the stored C to the open verifier's independent ``f``:
``core.C == crash_point(reproduce_round(seed, roundId, roundNumber), edge)`` —
server == verifier by construction (the verifier imports ``engine`` verbatim).
"""

from __future__ import annotations

import math

import pytest

from app.ws.crash_core import CrashRound, crash_point_for_round
from engine.games._curve import crash_point
from engine.rng import create_rng
from verifier import reproduce_round

EDGE = 0.01  # the S18 DEFAULT_CRASH_EDGE (1% house edge → RTP 0.99 at any target)

# 5σ → ~6e-7 two-sided false-reject rate; deterministic under the fixed seed below
# (no RNG-driven flakes). Matches the rtp_harness / sibling Originals gates.
_Z = 5.0

# Fixed per-round server seed: rounds vary only by (round_id, round_number), so the
# HMAC message differs each round and the gate is a deterministic function of n.
_SEED = b"crash-loop-server-seed"

# Fast unmarked sample — THIS is the real PR gate (the heavy 1e6 variant is marked
# rtp_heavy and EXCLUDED by `addopts = -m 'not rtp_heavy'`, so it does not run under
# the Verify command). Sized so the 5σ bands stay tight: P(C>=2) band ≈ ±0.0056,
# P(f<edge) band ≈ ±0.0011 — the latter rejects the ≈0.0198 floored-mass trap.
_FAST_N = 200_000
_HEAVY_N = 1_000_000


def _round_id(i: int) -> str:
    """The public per-round salt the core/actor uses (clientSeed of the round)."""
    return f"round-{i}"


def _band(p: float, n: int) -> float:
    """5σ binomial CLT half-width for a probability ``p`` over ``n`` trials."""
    return _Z * math.sqrt(p * (1.0 - p) / n)


def _sample(n: int) -> dict[str, float]:
    """One pass over ``n`` rounds; returns measured survival + bust fractions.

    Drives the ACTUAL core path (``CrashRound.open`` → ``.C``) for survival, and
    measures the bust rate on the continuous draw ``f`` (the same derivation the
    core consumes), asserting per-round that ``crash_point(f, edge) == core.C`` so
    the f used for the bust rate is provably the f the core mapped to C.
    """
    survive2 = survive10 = busts = 0
    for i in range(n):
        round_id = _round_id(i)
        rnd = CrashRound.open(
            round_server_seed=_SEED, round_id=round_id, round_number=i, edge=EDGE
        )
        f = create_rng(_SEED, client_seed=round_id, nonce=i).next()
        assert crash_point(f, EDGE) == rnd.C  # the core mapped THIS f to C
        if rnd.C >= 2.0:
            survive2 += 1
        if rnd.C >= 10.0:
            survive10 += 1
        if f < EDGE:
            busts += 1
    return {
        "survive2": survive2 / n,
        "survive10": survive10 / n,
        "bust": busts / n,
    }


def _assert_distribution(s: dict[str, float], n: int) -> None:
    assert abs(s["survive2"] - (1.0 - EDGE) / 2.0) <= _band((1.0 - EDGE) / 2.0, n)
    assert abs(s["survive10"] - (1.0 - EDGE) / 10.0) <= _band((1.0 - EDGE) / 10.0, n)
    assert abs(s["bust"] - EDGE) <= _band(EDGE, n)


def test_crash_C_distribution_fast() -> None:
    """Survival P(C>=2)=0.495, P(C>=10)=0.099, bust P(f<edge)=edge within 5σ bands."""
    _assert_distribution(_sample(_FAST_N), _FAST_N)


@pytest.mark.rtp_heavy
def test_crash_C_distribution_heavy() -> None:
    """1e6 trials (rtp-gate / nightly): the survival + bust identities converge."""
    s = _sample(_HEAVY_N)
    _assert_distribution(s, _HEAVY_N)
    print(
        f"\n[crash dist] n={_HEAVY_N} P(C>=2)={s['survive2']:.6f} (exp 0.495000) "
        f"P(C>=10)={s['survive10']:.6f} (exp 0.099000) "
        f"P(f<edge)={s['bust']:.6f} (exp {EDGE:.6f})"
    )


def test_instant_bust_is_continuous_not_floored_mass() -> None:
    """The bust rate is P(f<edge)≈edge, NOT P(C==1.00)≈2·edge.

    Discriminating: a derivation that measured the floored mass would report
    ≈0.0198 and FAIL the edge assertion. We assert the floored mass really is
    ≈2·edge here so the distinction is not vacuous.
    """
    n = _FAST_N
    busts = floored = 0
    for i in range(n):
        round_id = _round_id(i)
        f = create_rng(_SEED, client_seed=round_id, nonce=i).next()
        if f < EDGE:
            busts += 1
        if crash_point(f, EDGE) == 1.00:
            floored += 1
    bust_rate = busts / n
    floored_rate = floored / n
    assert abs(bust_rate - EDGE) <= _band(EDGE, n)
    # floored mass ≈ 1 - (1-edge)/1.01 ≈ 0.0198 — measurably ABOVE the bust rate.
    expected_floored = 1.0 - (1.0 - EDGE) / 1.01
    assert abs(floored_rate - expected_floored) <= _band(expected_floored, n)
    assert floored_rate > bust_rate + 0.005  # the two masses are distinct


def test_core_C_equals_verifier_reproduce_round() -> None:
    """server == verifier: the core's stored C equals crash_point of the verifier's
    independently reproduced per-round f (the verifier imports ``engine`` verbatim).
    """
    for i in range(200):
        round_id = _round_id(i)
        rnd = CrashRound.open(
            round_server_seed=_SEED, round_id=round_id, round_number=i, edge=EDGE
        )
        f = reproduce_round(server_seed=_SEED, round_id=round_id, round_number=i)
        assert rnd.C == crash_point(f, EDGE)


def test_crash_point_for_round_is_deterministic() -> None:
    """Same (seed, roundId, roundNumber) → same C, always."""
    a = crash_point_for_round(_SEED, "round-7", 7, EDGE)
    b = crash_point_for_round(_SEED, "round-7", 7, EDGE)
    assert a == b
