"""Pure sampling helpers driven by the seeded ``RngStream`` (spec §2.4).

``sample_without_replacement`` draws ``k`` distinct items from a population using a
partial Fisher–Yates selection sample — exactly the §2.4 ``sampleWithoutRepl(N, k)``
helper used by Keno (40→10), and reusable by Mines (25→M) and Roulette. It consumes
EXACTLY ``k`` draws from the stream (one per selected item), so the verifier
reproduces the same subset bit-for-bit.

Pure: stdlib only (``math.floor``); entropy enters solely via the injected
``RngStream``. Same ``(server, client, nonce)`` stream → same subset, always.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import floor

from engine.types import RngStream


def sample_without_replacement[T](
    rng: RngStream, population: Sequence[T], k: int
) -> list[T]:
    """Draw ``k`` distinct items from ``population`` in draw order (spec §2.4).

    Partial Fisher–Yates: for each of the first ``k`` positions, swap in a uniformly
    chosen remaining element. Consumes exactly ``k`` ``rng.next()`` draws. The
    returned list preserves draw order; callers comparing membership use a set.

    Raises ``ValueError`` when ``k`` is outside ``[0, len(population)]`` (a structural
    guard — callers fence game input upstream).
    """
    pool = list(population)
    n = len(pool)
    if not 0 <= k <= n:
        raise ValueError(f"cannot draw {k} distinct from a population of {n}")
    result: list[T] = []
    for i in range(k):
        # Uniform index in [i, n): floor(f * (n - i)) ∈ {0, …, n-i-1} since f ∈ [0,1).
        j = i + floor(rng.next() * (n - i))
        pool[i], pool[j] = pool[j], pool[i]
        result.append(pool[i])
    return result
