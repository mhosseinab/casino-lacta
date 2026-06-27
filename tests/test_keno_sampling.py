"""S15 — ``sample_without_replacement`` (spec §2.4): pure, seeded, exactly-k draws.

The shared draw helper Keno uses (40→10) and Mines/Roulette can reuse. Tested
directly (not only via Keno) so a biased sampler is caught here; the keno
hit-frequency test (test_keno_rtp.py) is the independent statistical check that the
*subset* is uniform.
"""

from __future__ import annotations

import pytest

from engine.rng import create_rng
from engine.sampling import sample_without_replacement

SERVER_SEED = b"keno-sampling-server-seed"
CLIENT_SEED = "player-client-seed"


def test_draws_exactly_k_distinct_in_range() -> None:
    drawn = sample_without_replacement(create_rng(SERVER_SEED, CLIENT_SEED, 0), range(1, 41), 10)
    assert len(drawn) == 10
    assert len(set(drawn)) == 10  # distinct
    assert all(1 <= x <= 40 for x in drawn)


def test_consumes_exactly_k_draws() -> None:
    """One ``rng.next()`` per selected item — so the verifier reproduces the subset."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 0)
    sample_without_replacement(rng, range(1, 41), 10)
    assert rng.cursor == 10


def test_deterministic_under_fixed_seed() -> None:
    a = sample_without_replacement(create_rng(SERVER_SEED, CLIENT_SEED, 5), range(1, 41), 10)
    b = sample_without_replacement(create_rng(SERVER_SEED, CLIENT_SEED, 5), range(1, 41), 10)
    assert a == b


def test_distinct_nonces_diverge() -> None:
    subsets = set()
    for n in range(20):
        rng = create_rng(SERVER_SEED, CLIENT_SEED, n)
        subsets.add(tuple(sorted(sample_without_replacement(rng, range(1, 41), 10))))
    assert len(subsets) > 1


def test_full_population_is_a_permutation() -> None:
    """k == n yields a permutation of the whole population (no item lost/duplicated)."""
    pop = list(range(1, 41))
    perm = sample_without_replacement(create_rng(SERVER_SEED, CLIENT_SEED, 1), pop, len(pop))
    assert sorted(perm) == pop


@pytest.mark.parametrize("k", [-1, 41])
def test_rejects_out_of_bounds_k(k: int) -> None:
    with pytest.raises(ValueError):
        sample_without_replacement(create_rng(SERVER_SEED, CLIENT_SEED, 0), range(1, 41), k)


def test_zero_draws_is_empty_and_consumes_nothing() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 0)
    assert sample_without_replacement(rng, range(1, 41), 0) == []
    assert rng.cursor == 0
