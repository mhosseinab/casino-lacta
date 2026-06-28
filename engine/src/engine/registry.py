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
from engine.table.blackjack import DEFAULT_PARAMS as _blackjack_default_params
from engine.table.roulette_wheel import default_params as _roulette_wheel_default_params
from engine.table.video_poker import default_params as _video_poker_default_params
from engine.types import GameConfig, InstantGame, StatefulGame

# Plinko (spec §A.5) per-(rows, risk) multiplier tables — TUNED so the analytic
# RTP ``Σ_i C(R,i)/2^R · m_i`` equals ``1 - edge`` AFTER 2-dp rounding (within
# ±0.2% for every combo; verified by tests/test_plinko_rtp.py). Symmetric and
# convex (high at the edges, low in the centre). Row keys are JSON strings so the
# table survives the DB GameConfig.params round-trip unchanged. Re-tune these
# numbers (never clamp outcomes) if the edge or supported boards change.
_PLINKO_TABLES: dict[str, dict[str, list[float]]] = {
    "8": {
        "LOW": [1.62, 1.34, 1.14, 0.96, 0.82, 0.96, 1.14, 1.34, 1.62],
        "MEDIUM": [2.6, 1.83, 1.29, 0.91, 0.64, 0.91, 1.29, 1.83, 2.6],
        "HIGH": [5.33, 2.81, 1.46, 0.78, 0.41, 0.78, 1.46, 2.81, 5.33],
    },
    "12": {
        "LOW": [1.84, 1.57, 1.37, 1.22, 1.06, 0.94, 0.82, 0.94, 1.06, 1.22, 1.37, 1.57, 1.84],
        "MEDIUM": [3.46, 2.59, 1.96, 1.49, 1.12, 0.86, 0.65, 0.86, 1.12, 1.49, 1.96, 2.59, 3.46],
        "HIGH": [9.62, 5.67, 3.35, 1.96, 1.16, 0.68, 0.4, 0.68, 1.16, 1.96, 3.35, 5.67, 9.62],
    },
    "16": {
        "LOW": [1.96, 1.74, 1.54, 1.42, 1.26, 1.15, 1.03, 0.92, 0.83, 0.92, 1.03, 1.15, 1.26, 1.42, 1.54, 1.74, 1.96],  # noqa: E501
        "MEDIUM": [4.19, 3.36, 2.65, 2.09, 1.66, 1.31, 1.05, 0.83, 0.66, 0.83, 1.05, 1.31, 1.66, 2.09, 2.65, 3.36, 4.19],  # noqa: E501
        "HIGH": [13.99, 9.05, 5.83, 3.77, 2.41, 1.57, 1.01, 0.65, 0.42, 0.65, 1.01, 1.57, 2.41, 3.77, 5.83, 9.05, 13.99],  # noqa: E501
    },
}

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
    # Pocket Dice (spec §A.2): bet on the sum of 2d6 (2-12), OVER/UNDER a target;
    # multiplier = (1-edge)/p from the triangular pmf, RTP = 1-edge every target.
    # Default edge 0.01 mirrors the sibling originals.* GameConfig rows.
    "originals.pocketdice": (
        "engine.games.pocketdice",
        GameConfig(edge=0.01, params={}),
    ),
    # Roulette 0–99 (spec §A.9): one draw → result = floor(f·100); map result→colour
    # via the configurable `pockets` table (1 green / 49 red / 50 black default);
    # each placed colour bet pays (1-edge)/p, per-bet RTP = 1-edge. Default edge 0.01.
    "originals.roulette": (
        "engine.games.roulette99",
        GameConfig(
            edge=0.01,
            params={
                "pockets": {
                    "GREEN": [0],
                    "RED": list(range(1, 50)),
                    "BLACK": list(range(50, 100)),
                }
            },
        ),
    ),
    # Plinko (spec §A.5): R rows of pegs → bin ~ Binomial(R,½); bin indexes a tuned
    # per-(rows,risk) multiplier table (Σ P(i)·m_i = 1-edge post-rounding). Default
    # edge 0.01; tables carried in params (config, not literals in play()).
    "originals.plinko": (
        "engine.games.plinko",
        GameConfig(edge=0.01, params={"tables": _PLINKO_TABLES}),
    ),
    # Keno (spec §A.8): pick 1–10 of 1–40; draw 10; hypergeometric hits → a tuned
    # per-(picks, risk) payout table (in params, configurable) with Σ P(h)·pay_h =
    # 1-edge. Default edge 0.01; default tables seeded from engine.games.keno.
    "originals.keno": (
        "engine.games.keno",
        GameConfig(edge=0.01, params=_keno_default_params()),
    ),
    # Mines (spec §A.6): the first StatefulGame. M hidden mines on a 25-cell grid,
    # committed via sampleWithoutReplacement(25, M) at round start; after k safe
    # reveals payout = (1-edge)·C(25,k)/C(25-M,k), so EV(cash) = 1-edge for any (M,k).
    # M is per-bet input (not config); default edge 0.01, no game params.
    "originals.mines": ("engine.games.mines", GameConfig(edge=0.01, params={})),
    # HiLo (spec §A.7): the second StatefulGame, first to draw fresh entropy per action.
    # Each next card draws floor(f*52)//4+1 (uniform rank 1..13); a correct Higher-/
    # Lower-or-same call compounds (1-edge)/p(side); each step EV = 1-edge. No game
    # params; default edge 0.01. The cumulative multiplier is unbounded in principle —
    # the real ceiling is the GOLD GameLimit max_win (enforced via engine.money.cap).
    "originals.hilo": ("engine.games.hilo", GameConfig(edge=0.01, params={})),
    # European Roulette (spec §B.3): single-zero wheel, 37 pockets; one draw →
    # pocket = floor(f·37); every inside/outside bet settles via the payout table in
    # params (straight 35:1 … even-money 1:1), so per-bet RTP = (count/37)·(payout+1)
    # = 36/37 by construction — pocket 0 is the whole 1/37 ≈ 2.70% edge. DISTINCT from
    # the originals.roulette 0–99 colour-pick game (different wheel, different id).
    "table.roulette": (
        "engine.table.roulette_wheel",
        GameConfig(edge=1.0 / 37.0, params=_roulette_wheel_default_params()),
    ),
    # Baccarat (punto banco, spec §B.4): an InstantGame (no player decisions) — deal
    # Player/Banker from a finite shoe, apply the fixed third-card tableau, settle
    # PLAYER/BANKER/TIE. Banker win pays 1:1 minus `bankerCommission`; TIE pays
    # `tiePayout`:1; PLAYER/BANKER push on a tie. The edges are EMERGENT from the
    # rules (not a tunable `edge`): for the defaults below, Banker ≈ 1.06% / Player
    # ≈ 1.24% / Tie ≈ 14.36% (certified by exact enumeration). `edge` here is the
    # headline (Banker) house edge, informational only — play() never reads it.
    "table.baccarat": (
        "engine.table.baccarat",
        GameConfig(
            edge=0.0106,
            params={"decks": 8, "bankerCommission": 0.05, "tiePayout": 8},
        ),
    ),
    # Video Poker (spec §B.5): 5-card draw, 9/6 Jacks-or-Better. A StatefulGame — init
    # commits 10 distinct cards (dealt 5 + 5 replacements) from one stream; the draw
    # action's `holds` keep cards, discards are filled from the committed pool; the final
    # hand is ranked by the shared cards.evaluator and mapped to the paytable. RTP emerges
    # from the paytable (config, in params — not a clamp); ≈99.54% under optimal play, so
    # edge≈0.0046 is informational (the engine reads the paytable, never `edge`).
    "table.video_poker": (
        "engine.table.video_poker",
        GameConfig(edge=0.0046, params=_video_poker_default_params()),
    ),
    # Blackjack (table): the first StatefulGame table game. D-deck shoe committed at
    # round open, drawn without replacement progressively from the stream; hit/stand/
    # double/split/insurance/(surrender), dealer plays per S17. Payouts are rule-FIXED
    # (3:2 BJ, even-money win, push) — edge is EMERGENT (the published ~0.40% house
    # edge for the default rule set), documentation only, never applied to a payout.
    # All rules live in params (config, not literals); see engine.table.blackjack.
    "table.blackjack": (
        "engine.table.blackjack",
        GameConfig(edge=0.004, params=_blackjack_default_params),
    ),
    # Slots — machine01 (spec §B.1). ONE config-driven framework (engine.slots.framework
    # GAME) serves EVERY machine: machine identity is THIS registry key + the DB
    # GameConfig, never per-machine engine code (S24 adds machine02/03 as new entries
    # pointing here + their own config — "config + art, not code"). The strips/paytable/
    # paylines/feature live in engine/src/engine/slots/machines/machine01.json as INERT
    # DATA — engine purity forbids reading a file here, so the registry default carries
    # NO params; the JSON is loaded app-side (app.slots.load_machine) and seeded into the
    # authoritative DB GameConfig.params (the sole runtime authority — no Python-literal
    # copy to drift from). ``edge`` mirrors the JSON house edge (RTP 0.96), informational:
    # slot RTP emerges from the strips/paytable, never from ``edge``.
    "slots.machine01": ("engine.slots.framework", GameConfig(edge=0.04, params={})),
    # Slots — machine02 (LOW volatility) + machine03 (HIGH volatility), spec §B.1.
    # The S24 proof of "new machine = config + art, not code": BOTH point at the SAME
    # engine.slots.framework GAME with EMPTY params (no per-machine engine code, no
    # Python-literal params copy to drift). Each machine's strips/paytable/paylines/
    # feature live as INERT DATA in its JSON (machine0{2,3}.json), loaded app-side
    # (app.slots.load_machine) and seeded into the authoritative DB GameConfig.params
    # (the sole runtime authority). Both are tuned to RTP 0.96 (edge 0.04, informational);
    # they differ ONLY in volatility (per-spin payout variance), which is config, not code.
    "slots.machine02": ("engine.slots.framework", GameConfig(edge=0.04, params={})),
    "slots.machine03": ("engine.slots.framework", GameConfig(edge=0.04, params={})),
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
