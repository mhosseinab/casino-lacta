"""Commit / reveal / verify — the provably-fair primitives for Originals.

Commit–reveal (spec §2.3):

  * ``commit(server_seed)`` publishes ``SHA256(server_seed)`` BEFORE the bet, so
    the player holds a binding commitment to the unrevealed seed.
  * On seed rotation the ``server_seed`` is revealed; any past outcome is then
    recomputed from ``(server_seed, client_seed, nonce)`` via ``verify``.

``verify`` rebuilds the exact same ``RngStream`` and re-runs the game's pure
derivation, so an independent party reproduces the server's result bit-for-bit.
Stdlib only — purity is a dependency fact.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from engine.rng import HmacRngStream, create_rng


def commit(server_seed: bytes) -> str:
    """Return ``SHA256(server_seed)`` as hex — the pre-bet commitment."""
    return hashlib.sha256(server_seed).hexdigest()


def verify[Outcome](
    server_seed: bytes,
    client_seed: str,
    nonce: int,
    derive_fn: Callable[[HmacRngStream], Outcome],
) -> Outcome:
    """Recompute an outcome from the revealed seed via the game's derivation.

    ``derive_fn`` is the same pure mapping the server used (it consumes the
    stream and returns the outcome), so the result equals the server's by
    construction — this is the verifier-parity primitive.
    """
    rng = create_rng(server_seed, client_seed, nonce)
    return derive_fn(rng)
