"""S4 — HMAC-SHA256 RNG stream + commit/reveal fairness.

Engine purity: these exercise pure, deterministic outcome logic — no IO, no
wall-clock, no `random`. Same `(server_seed, client_seed, nonce)` → same stream.
"""

from __future__ import annotations

import hashlib
import hmac

from engine.fairness import commit, verify
from engine.rng import create_rng


def _draw(server_seed: bytes, client_seed: str, nonce: int, count: int) -> list[float]:
    rng = create_rng(server_seed, client_seed, nonce)
    return [rng.next() for _ in range(count)]


def test_next_is_uniform_unit_interval() -> None:
    rng = create_rng(b"server", "client", 0)
    for _ in range(1000):
        x = rng.next()
        assert 0.0 <= x < 1.0


def test_matches_spec_derivation() -> None:
    """First float = uint32(first 4 bytes of HMAC) / 2**32 (spec §2.3)."""
    server_seed = b"server-seed"
    client_seed = "player-1"
    nonce = 7
    digest = hmac.new(
        server_seed, f"{client_seed}:{nonce}:0".encode(), hashlib.sha256
    ).digest()
    expected = int.from_bytes(digest[:4], "big") / 2**32

    rng = create_rng(server_seed, client_seed, nonce)
    assert rng.next() == expected


def test_determinism_same_inputs_same_stream() -> None:
    a = _draw(b"seed-A", "client-x", 42, 50)
    b = _draw(b"seed-A", "client-x", 42, 50)
    assert a == b


def test_distinct_inputs_diverge() -> None:
    base = _draw(b"seed-A", "client-x", 42, 10)
    assert base != _draw(b"seed-B", "client-x", 42, 10)  # server_seed
    assert base != _draw(b"seed-A", "client-y", 42, 10)  # client_seed
    assert base != _draw(b"seed-A", "client-x", 43, 10)  # nonce


def test_cursor_advances_within_a_stream() -> None:
    rng = create_rng(b"seed", "c", 0)
    first, second = rng.next(), rng.next()
    assert first != second


def test_uniformity_smoke_mean_near_half() -> None:
    rng = create_rng(b"uniformity", "smoke", 1)
    n = 100_000
    total = sum(rng.next() for _ in range(n))
    mean = total / n
    assert abs(mean - 0.5) < 0.01


def test_commit_is_sha256_hex_of_server_seed() -> None:
    server_seed = b"top-secret-server-seed"
    assert commit(server_seed) == hashlib.sha256(server_seed).hexdigest()


def test_commit_is_reproducible() -> None:
    server_seed = b"rotate-me"
    assert commit(server_seed) == commit(server_seed)
    assert len(commit(server_seed)) == 64


def test_verify_recomputes_outcome_from_seed() -> None:
    """verify() reproduces a past outcome via the same derive_fn (server==verifier)."""
    server_seed = b"reveal-on-rotation"
    client_seed = "audit"
    nonce = 99

    def derive(rng: object) -> int:
        # mirrors an Originals mapping: floor(f * 10000)
        return int(rng.next() * 10_000)  # type: ignore[attr-defined]

    # server-side outcome
    server_outcome = derive(create_rng(server_seed, client_seed, nonce))
    # independent recomputation from the revealed seed
    recomputed = verify(server_seed, client_seed, nonce, derive)
    assert recomputed == server_outcome
