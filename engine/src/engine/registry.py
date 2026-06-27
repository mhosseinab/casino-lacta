"""Game registry — id -> (module path, default GameConfig).

The OCP seam: a new game is a registry entry + a conforming outcome function; the
bet loop, ledger, and API never change. DB GameConfig/GameLimit are authoritative at
runtime; these are the defaults/seed values. §3.

Each registered module exposes a module-level ``GAME`` singleton conforming to
``engine.types.InstantGame`` / ``StatefulGame``; :func:`load_game` resolves it by
dotted path so the bet loop never imports a concrete game.
"""

from __future__ import annotations

import importlib
from typing import cast

from engine.games.keno import default_params as _keno_default_params
from engine.types import GameConfig, InstantGame, StatefulGame

# id -> (dotted module path of the game's outcome impl, default GameConfig)
REGISTRY: dict[str, tuple[str, GameConfig]] = {
    # A trivial 50/50 paying 1.98x → 0.99 RTP (1% edge). A test fixture that
    # exercises the shared bet loop end to end; distinct from the user-facing
    # originals.coinflip shipped in S17 (they coexist by design).
    "stub.coinflip": ("engine.games.stub_coinflip", GameConfig(edge=0.01, params={})),
    # Dice (spec §A.1): UNDER/OVER a target; multiplier = (1-edge)/p, RTP = 1-edge.
    # Default edge 0.01 mirrors the seeded originals.dice GameConfig row (S2).
    "originals.dice": ("engine.games.dice", GameConfig(edge=0.01, params={})),
    # Limbo (spec §A.3): pick a target multiplier; X = crash_point(f, edge) (the
    # shared §2.4 curve, carry-forward to Crash S18); win X >= target pays target,
    # RTP = 1-edge for every target. Default edge 0.01.
    "originals.limbo": ("engine.games.limbo", GameConfig(edge=0.01, params={})),
    # Keno (spec §A.8): pick 1–10 of 1–40; draw 10; hypergeometric hits → a tuned
    # per-(picks, risk) payout table (in params, configurable) with Σ P(h)·pay_h =
    # 1-edge. Default edge 0.01; default tables seeded from engine.games.keno.
    "originals.keno": (
        "engine.games.keno",
        GameConfig(edge=0.01, params=_keno_default_params()),
    ),
}


class UnknownGame(KeyError):
    """No registry entry for the given game id."""


def default_config(game_id: str) -> GameConfig:
    """The engine default/seed ``GameConfig`` for ``game_id`` (DB is authoritative at runtime)."""
    try:
        return REGISTRY[game_id][1]
    except KeyError as exc:
        raise UnknownGame(game_id) from exc


def load_game(game_id: str) -> InstantGame | StatefulGame:
    """Resolve the registered game's ``GAME`` singleton by its dotted module path."""
    try:
        module_path = REGISTRY[game_id][0]
    except KeyError as exc:
        raise UnknownGame(game_id) from exc
    module = importlib.import_module(module_path)
    return cast("InstantGame | StatefulGame", module.GAME)
