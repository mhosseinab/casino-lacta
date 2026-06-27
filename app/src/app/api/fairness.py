"""``GET /fairness/{betId}`` — the open commit-reveal disclosure (spec §2.10).

Returns the bet's fairness triple — ``serverSeedHash`` (the pre-bet commitment),
``clientSeed``, ``nonce`` — plus the derivation description and a link to the
open verifier. The ``serverSeed`` itself is revealed ONLY after the seed has been
rotated (``ServerSeed.rotated_at`` set): leaking an un-rotated server seed would
let a player predict future bets on that seed, so pre-reveal it is ``null`` and
only the commitment hash is exposed. The fairness snapshot is read from the
immutable ``AuditEvent`` (BET_SETTLED) the bet loop persisted — the canonical
record of what produced the outcome.

Transport-only: it reads and shapes, it decides nothing.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select

from app.api.games import _runtime  # shared lazy DB runtime (same app.state instance)
from app.db.models import AuditEvent, Bet, ServerSeed

router = APIRouter(tags=["fairness"])

# The per-user Originals derivation, surfaced verbatim in the disclosure so a
# player can follow the math. NOTE: ``app`` and ``verifier`` are independent
# sibling layers (the import-linter forbids app→verifier), so this transport-side
# description is owned here; the verifier package documents the same derivation
# for its own consumers. The single SOURCE OF TRUTH for the math is engine.rng,
# which both layers import.
_USER_DERIVATION = (
    "bytes = HMAC_SHA256(serverSeed, f'{clientSeed}:{nonce}:{cursor}'); "
    "float = uint32(bytes[:4]) / 2**32; cursor += 1 per draw "
    "(clientSeed = player client seed, nonce = per-(user,serverSeed) bet counter)"
)


def _verifier_link(
    *,
    game_id: str,
    client_seed: str,
    nonce: int,
    server_seed_hash: str,
    server_seed: str | None,
) -> str:
    """Relative link to the static verifier page (a deployment asset under
    ``/verifier/`` — app's own routing knowledge), prefilled with the bet's
    PUBLIC fairness fields. The raw ``server_seed`` is appended ONLY once revealed."""
    params: dict[str, str] = {
        "gameId": game_id,
        "clientSeed": client_seed,
        "nonce": str(nonce),
        "serverSeedHash": server_seed_hash,
    }
    if server_seed is not None:
        params["serverSeed"] = server_seed
    return f"/verifier/index.html?{urlencode(params)}"


class FairnessDisclosure(BaseModel):
    """The §2.10 fairness payload: seeds (+revealed), nonce, derivation, link."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    bet_id: str
    game_id: str
    server_seed_hash: str
    # Revealed (hex) ONLY post-rotation; ``None`` while the seed is still active.
    server_seed: str | None
    revealed: bool
    client_seed: str
    nonce: int
    derivation: str
    verifier_url: str


@router.get("/fairness/{bet_id}", response_model=FairnessDisclosure)
async def get_fairness(bet_id: str, request: Request) -> FairnessDisclosure:
    rt = _runtime(request)
    async with rt.session_factory() as session:
        bet = await session.get(Bet, bet_id)
        if bet is None:
            raise HTTPException(status_code=404, detail=f"unknown bet {bet_id}")
        audit = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.bet_id == bet_id, AuditEvent.type == "BET_SETTLED")
            .limit(1)
        )
        if audit is None or not audit.payload:
            raise HTTPException(status_code=404, detail=f"no fairness record for {bet_id}")
        payload = audit.payload
        seed = await session.get(ServerSeed, str(payload["serverSeedId"]))

    # Reveal the raw server seed ONLY once it has been rotated out of use.
    revealed = seed is not None and seed.rotated_at is not None
    server_seed_hex = seed.seed_encrypted if (revealed and seed is not None) else None

    game_id = str(payload["gameId"])
    server_seed_hash = str(payload["serverSeedHash"])
    client_seed = str(payload["clientSeed"])
    nonce = int(payload["nonce"])

    return FairnessDisclosure(
        bet_id=bet_id,
        game_id=game_id,
        server_seed_hash=server_seed_hash,
        server_seed=server_seed_hex,
        revealed=revealed,
        client_seed=client_seed,
        nonce=nonce,
        derivation=_USER_DERIVATION,
        verifier_url=_verifier_link(
            game_id=game_id,
            client_seed=client_seed,
            nonce=nonce,
            server_seed_hash=server_seed_hash,
            server_seed=server_seed_hex,
        ),
    )
