"""Shared crash/limbo multiplier curve — the ``P(X ≥ x) = (1 − edge)/x`` family.

A single pure helper, ``crash_point(f, edge)``, that maps one uniform draw
``f ∈ [0, 1)`` to a two-decimal multiplier ``X``. This is the **carry-forward**
seam (spec §2.4): Limbo (S10) calls it per-(user, serverSeed) bet, and Crash
(S18) reuses it VERBATIM per round — one implementation, never duplicated, so a
Limbo bet and a Crash round that derive the same ``f`` produce the same ``X``.

Pure: stdlib ``math`` only, no IO/clock/``random`` — entropy enters solely as the
``f`` the caller drew from the seeded stream.
"""

from __future__ import annotations

import math

# Two-decimal multiplier resolution: the raw ratio is scaled by 100, floored to a
# whole "centi-multiplier", then scaled back — so X is always a clean N.NN value.
_CENTI = 100

# The curve's lower clamp: X never drops below 1.00×. Two DISTINCT masses sit at
# the bottom — do not conflate them (this matters for the S18 Crash carry-forward,
# which decides what to test against `edge`):
#   * Continuous clamp mass — raw = (1 − edge)/(1 − f) < 1.00  ⟺  f < edge — has
#     probability exactly `edge`. THIS is the spec §A.4 "instant-bust rate ≈ edge":
#     the house edge realised as the fraction of draws whose raw ratio falls below
#     1.00× (equivalently P(raw < 1.00) = P(f < edge) = edge).
#   * Floored mass at EXACTLY 1.00 — because floor(raw*100)/100 == 1.00 for every
#     raw ∈ [1.00, 1.01), X == 1.00 whenever raw < 1.01  ⟺  f < 1 − (1 − edge)/1.01,
#     so P(X == 1.00) = 1 − (1 − edge)/1.01 ≈ 2·edge (≈ 0.0198 at edge = 0.01).
# So an empirical P(X == 1.00) measures ≈ 2·edge, NOT `edge`. To test the
# instant-bust RATE against `edge`, measure the continuous P(f < edge) =
# P(raw < 1.00), never the floored P(X == 1.00).
_FLOOR = 1.00


def crash_point(f: float, edge: float) -> float:
    """Map a uniform draw ``f ∈ [0, 1)`` to a two-decimal multiplier ``X ≥ 1.00``.

    ``X = max(1.00, floor((1 − edge) / (1 − f) * 100) / 100)`` (spec §2.4). The
    resulting distribution satisfies ``P(X ≥ x) = (1 − edge) / x`` for ``x > 1.00``,
    which gives every fixed target an RTP of exactly ``1 − edge``. ``1 − f > 0``
    for all ``f ∈ [0, 1)`` (the seeded stream never returns 1.0), so the division
    is always defined.
    """
    return max(_FLOOR, math.floor((1.0 - edge) / (1.0 - f) * _CENTI) / _CENTI)
