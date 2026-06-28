"""Open provably-fair verifier — imports ``engine`` verbatim, never a copy.

The load-bearing DRY constraint: this reference reproduces a server outcome by
calling the SAME engine code path the bet loop uses
(``engine.fairness.verify`` → ``engine.rng.create_rng`` → the registered game's
``play``). There is ONE outcome implementation; the verifier reuses it, so
``server result == verifier result`` by construction. If this module ever
recomputed game math itself, the verifier would stop proving the server.

Two derivation signatures (spec §2.3b, §A.4):

* **user / Originals** — ``reproduce(...)``: the per-(user, serverSeed) signature.
  ``clientSeed`` is the player's editable client seed, ``nonce`` the player's
  per-(user, serverSeed) monotonic bet counter; cursor starts at 0 and advances
  as the game consumes draws.
* **round / Crash** — ``reproduce_round(...)``: the per-ROUND signature.
  ``clientSeed = roundId`` (the public round salt), ``nonce = roundNumber``,
  ``cursor = 0`` — one float ``f`` per round (see S18). Structurally this is the
  same HMAC stream with the round's identifiers relabelled into the seam.
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

from engine.fairness import verify
from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InstantGame, Outcome, StatefulGame

# Human-readable derivation strings surfaced by GET /fairness (spec §2.10) so a
# player can see exactly how the byte stream maps to an outcome.
USER_DERIVATION = (
    "bytes = HMAC_SHA256(serverSeed, f'{clientSeed}:{nonce}:{cursor}'); "
    "float = uint32(bytes[:4]) / 2**32; cursor += 1 per draw "
    "(clientSeed = player client seed, nonce = per-(user,serverSeed) bet counter)"
)
ROUND_DERIVATION = (
    "bytes = HMAC_SHA256(roundServerSeed, f'{roundId}:{roundNumber}:0'); "
    "f = uint32(bytes[:4]) / 2**32 "
    "(clientSeed = roundId public salt, nonce = roundNumber, cursor = 0)"
)


def reproduce(
    *,
    server_seed: bytes,
    client_seed: str,
    nonce: int,
    game_id: str,
    input: dict[str, Any] | None = None,
    cfg: GameConfig | None = None,
) -> Outcome:
    """Recompute an Originals outcome from ``(serverSeed, clientSeed, nonce, input)``.

    Reuses ``engine.fairness.verify`` with the registered game's ``play`` as the
    derivation — the identical call the bet loop makes — so the result equals the
    server's bit-for-bit. ``cfg`` defaults to the engine registry default; pass
    the AUDITED ``GameConfig`` (edge + params from the bet's ``configVersion``) to
    reproduce a specific historical bet exactly.
    """
    game = load_game(game_id)
    if not hasattr(game, "play"):  # stateful games replay via their own init/step
        raise ValueError(f"{game_id!r} is not an instant game; use the stateful replay path")
    instant = cast("InstantGame", game)
    config = cfg if cfg is not None else default_config(game_id)
    game_input = dict(input or {})
    return verify(
        server_seed,
        client_seed,
        nonce,
        lambda rng: instant.play(game_input, rng, config),
    )


def reproduce_stateful_init(
    *,
    server_seed: bytes,
    client_seed: str,
    nonce: int,
    game_id: str,
    input: dict[str, Any] | None = None,
    cfg: GameConfig | None = None,
) -> dict[str, Any]:
    """Recompute a ``StatefulGame``'s COMMITTED round-start state (e.g. the Mines mine
    layout) from ``(serverSeed, clientSeed, nonce, input)``.

    Reuses ``engine.fairness.verify`` with the registered game's ``init`` as the
    derivation — the identical call the bet loop makes at round open — so the
    reproduced state equals the server's bit-for-bit. Used post-reveal to prove the
    hidden layout was fixed before any action (never moved after the player's clicks).
    ``cfg`` defaults to the engine registry default; pass the AUDITED ``GameConfig`` to
    reproduce a specific historical round exactly.
    """
    game = load_game(game_id)
    if not hasattr(game, "init"):  # instant games replay via reproduce()
        raise ValueError(f"{game_id!r} is not a stateful game; use reproduce()")
    stateful = cast("StatefulGame", game)
    config = cfg if cfg is not None else default_config(game_id)
    game_input = dict(input or {})
    return verify(
        server_seed,
        client_seed,
        nonce,
        lambda rng: stateful.init(game_input, rng, config),
    )


def reproduce_stateful_sequence(
    *,
    server_seed: bytes,
    client_seed: str,
    nonce: int,
    game_id: str,
    actions: list[dict[str, Any]],
    input: dict[str, Any] | None = None,
    cfg: GameConfig | None = None,
) -> tuple[dict[str, Any], list[Outcome | None]]:
    """Replay a ``StatefulGame``'s FULL round — ``init`` then the whole step SEQUENCE —
    driving ONE seeded stream forward through the given ``actions``.

    This is the load-bearing parity for games that draw fresh entropy per action
    (HiLo's next card): the verifier reuses ``engine`` verbatim, constructing the same
    stream the server opened the round with and advancing it through the identical
    actions, so the drawn cards / cumulative multipliers equal the server's bit-for-bit.
    Reproducing only ``init`` (the first card) would be vacuous on the part that matters
    — the per-step draws. Returns ``(final_state, [outcome per action])``; ``cfg``
    defaults to the engine registry default (pass the AUDITED config for a historical
    round).
    """
    game = load_game(game_id)
    if not hasattr(game, "init"):  # instant games replay via reproduce()
        raise ValueError(f"{game_id!r} is not a stateful game; use reproduce()")
    stateful = cast("StatefulGame", game)
    config = cfg if cfg is not None else default_config(game_id)
    rng = create_rng(server_seed, client_seed, nonce)
    state = stateful.init(dict(input or {}), rng, config)
    outcomes: list[Outcome | None] = []
    for action in actions:
        state, outcome = stateful.step(state, action, rng)
        outcomes.append(outcome)
    return state, outcomes


def reproduce_round(
    *,
    server_seed: bytes,
    round_id: str,
    round_number: int,
) -> float:
    """Reproduce the per-round Crash float ``f`` (spec §A.4, §2.3b).

    The per-ROUND signature: ``clientSeed = roundId``, ``nonce = roundNumber``,
    ``cursor = 0``. Returns the single uniform ``f`` the round committed to; S18's
    curve maps ``f → C``. Reuses ``engine.fairness.verify`` (same HMAC stream),
    never a re-derivation.
    """
    return verify(server_seed, round_id, round_number, lambda rng: rng.next())


def verifier_link(
    *,
    game_id: str,
    client_seed: str,
    nonce: int,
    server_seed_hash: str,
    server_seed: str | None = None,
) -> str:
    """A relative link to the static verifier page, prefilled with the bet's
    public fairness fields. ``server_seed`` (hex) is included ONLY once revealed
    (post-rotation); pre-reveal the commitment ``server_seed_hash`` stands in."""
    params: dict[str, str] = {
        "gameId": game_id,
        "clientSeed": client_seed,
        "nonce": str(nonce),
        "serverSeedHash": server_seed_hash,
    }
    if server_seed is not None:
        params["serverSeed"] = server_seed
    return f"/verifier/index.html?{urlencode(params)}"


__all__ = [
    "ROUND_DERIVATION",
    "USER_DERIVATION",
    "reproduce",
    "reproduce_round",
    "reproduce_stateful_init",
    "reproduce_stateful_sequence",
    "verifier_link",
]
