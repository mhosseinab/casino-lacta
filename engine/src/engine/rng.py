"""HMAC-SHA256 seeded RNG stream — the single entropy source for all outcomes.

Provably-fair derivation (spec §2.3):

    bytes  = HMAC_SHA256(server_seed, f"{client_seed}:{nonce}:{cursor}")
    float  = uint32(bytes[:4]) / 2**32          # uniform [0, 1)
    cursor += 1                                  # bump for the next draw

The stream is a *pure function* of ``(server_seed, client_seed, nonce)``: same
inputs → same sequence, always. Entropy enters ONLY here — engine code never
touches ``random``/``secrets``; seed *generation* (CSPRNG) lives in ``app/``.
The verifier imports this module verbatim, so server == verifier by construction.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

# Each draw consumes the first 4 bytes of one HMAC digest → a 32-bit value.
_FLOAT_BYTES = 4
_UINT32_DENOM = 2**32


@dataclass
class HmacRngStream:
    """A deterministic ``RngStream``: ``next()`` yields uniform floats in [0, 1).

    Structurally conforms to ``engine.types.RngStream`` (``def next() -> float``);
    it is intentionally not imported here to keep this module free of internal
    coupling — the protocol is satisfied by shape.
    """

    server_seed: bytes
    client_seed: str
    nonce: int
    cursor: int = 0

    def next(self) -> float:
        """Return the next uniform float in [0, 1) and advance the cursor."""
        message = f"{self.client_seed}:{self.nonce}:{self.cursor}".encode()
        digest = hmac.new(self.server_seed, message, hashlib.sha256).digest()
        value = int.from_bytes(digest[:_FLOAT_BYTES], "big")
        self.cursor += 1
        return value / _UINT32_DENOM


def create_rng(server_seed: bytes, client_seed: str, nonce: int) -> HmacRngStream:
    """Construct the deterministic stream for one ``(server, client, nonce)`` bet."""
    return HmacRngStream(server_seed=server_seed, client_seed=client_seed, nonce=nonce)
