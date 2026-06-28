"""seed configs/limits for the five instant Originals (Limbo, Pocket Dice, Plinko,
Keno, Roulette 0–99)

These games have engine impls + ``engine.registry`` defaults but were never
seeded into the authoritative runtime tables, so the shared bet loop rejected
them (``GameDisabled`` / no limit) and they were not API-playable. This migration
adds ONLY data rows (no DDL): one ``GameConfig`` + one GOLD ``GameLimit`` per
game, making P2 fully demoable.

All five share ``edge = 0.01`` (RTP 0.99), ``version = 1``, ``enabled = True``,
``min_bet = 1`` GOLD. The data-driven ``params`` (Plinko/Keno tuned tables,
Roulette pocket map) are SOURCED at migration time from
``engine.registry.default_config`` — the reviewed single source of truth — so the
seed can never drift from the engine (asserted by
``tests/test_seed_instant_originals.py``).

Per-game GameLimit choices (minor units; ``max_multiplier`` informational only —
the ENFORCED ceiling is ``max_win``, applied via ``engine.money.cap`` AFTER floor):

* ``originals.limbo``    max_bet 100_000_000, max_win 1_000_000_000, max_mult 1e6.
  Limbo targets up to 1e6×; the extreme-jackpot tail caps at max_win by design
  (a documented product limit — common low-target wins are uncapped).
* ``originals.pocketdice`` max_bet 100_000_000, max_win 1_000_000_000, max_mult 36.
  Top win is 35.64× (sum hit at p=1/36); the rare top win at max stake caps at
  max_win (acceptable product limit; common low-mult wins are uncapped).
* ``originals.plinko``   max_bet 100_000, max_win 1_400_000, max_mult 14. Top
  table multiplier is 13.99× (rows=16, HIGH); 100_000·13.99 = 1_399_000 ≤
  max_win, so NOTHING is ever capped.
* ``originals.keno``     max_bet 100_000, max_win 6_000_000_000, max_mult 60_000.
  Top HIGH pick-10 payout ≈ 57_551.55×; 100_000·57_551.55 ≈ 5.755e9 ≤ max_win,
  so even the headline jackpot pays in full (P≈1e-9). max_win COVERS the top win.
* ``originals.roulette`` max_bet 10_000_000, max_win 1_000_000_000, max_mult 99.
  THE CAP TRAP: green pays ~99× and is a COMMON outcome (1/100), so max_win MUST
  be ≥ max_bet·99 or ordinary green wins are silently capped and realized RTP is
  depressed. Here 10_000_000·99 = 990_000_000 ≤ 1_000_000_000 — the invariant
  holds, so no green win is ever capped.

Down: delete exactly these five config + five limit rows by game_id, nothing
else. Linear chain (down_revision = d4e5f6a7b8c9).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from engine.registry import default_config

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAME_IDS = [
    "originals.limbo",
    "originals.pocketdice",
    "originals.plinko",
    "originals.keno",
    "originals.roulette",
]


def upgrade() -> None:
    game_configs = sa.table(
        "game_configs",
        sa.column("game_id", sa.String),
        sa.column("version", sa.Integer),
        sa.column("edge", sa.Numeric),
        sa.column("params", sa.JSON),
        sa.column("rtp", sa.Numeric),
        sa.column("volatility", sa.String),
        sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(
        game_configs,
        [
            {
                "game_id": "originals.limbo",
                "version": 1,
                "edge": Decimal("0.010000"),
                "params": {},
                "rtp": Decimal("0.990000"),
                "volatility": "high",
                "enabled": True,
            },
            {
                "game_id": "originals.pocketdice",
                "version": 1,
                "edge": Decimal("0.010000"),
                "params": {},
                "rtp": Decimal("0.990000"),
                "volatility": "medium",
                "enabled": True,
            },
            {
                "game_id": "originals.plinko",
                "version": 1,
                "edge": Decimal("0.010000"),
                # Tuned per-(rows, risk) multiplier tables — sourced from the
                # reviewed engine default (single source of truth, no drift).
                "params": default_config("originals.plinko").params,
                "rtp": Decimal("0.990000"),
                "volatility": "medium",
                "enabled": True,
            },
            {
                "game_id": "originals.keno",
                "version": 1,
                "edge": Decimal("0.010000"),
                # Tuned per-(picks, risk) payout tables — sourced from the engine default.
                "params": default_config("originals.keno").params,
                "rtp": Decimal("0.990000"),
                "volatility": "high",
                "enabled": True,
            },
            {
                "game_id": "originals.roulette",
                "version": 1,
                "edge": Decimal("0.010000"),
                # The colour→pocket map (1 green / 49 red / 50 black) — REQUIRED, or
                # play() KeyErrors on the missing pockets. Sourced from the engine default.
                "params": default_config("originals.roulette").params,
                "rtp": Decimal("0.990000"),
                "volatility": "medium",
                "enabled": True,
            },
        ],
    )

    game_limits = sa.table(
        "game_limits",
        sa.column("game_id", sa.String),
        sa.column("currency", sa.String),
        sa.column("min_bet", sa.BigInteger),
        sa.column("max_bet", sa.BigInteger),
        sa.column("max_win", sa.BigInteger),
        sa.column("max_multiplier", sa.Numeric),
    )
    op.bulk_insert(
        game_limits,
        [
            {
                "game_id": "originals.limbo",
                "currency": "GOLD",
                "min_bet": 1,
                "max_bet": 100_000_000,
                # The enforced ceiling: extreme jackpots cap here (documented limit).
                "max_win": 1_000_000_000,
                # Limbo targets up to 1e6×. Informational — max_win is enforced.
                "max_multiplier": Decimal("1000000.00000000"),
            },
            {
                "game_id": "originals.pocketdice",
                "currency": "GOLD",
                "min_bet": 1,
                "max_bet": 100_000_000,
                "max_win": 1_000_000_000,
                # Top win 35.64× at p=1/36. Informational — max_win is enforced.
                "max_multiplier": Decimal("36.00000000"),
            },
            {
                "game_id": "originals.plinko",
                "currency": "GOLD",
                "min_bet": 1,
                "max_bet": 100_000,
                # 100_000·13.99 = 1_399_000 ≤ max_win → nothing is ever capped.
                "max_win": 1_400_000,
                # Top table multiplier 13.99× (rows=16, HIGH). Informational.
                "max_multiplier": Decimal("14.00000000"),
            },
            {
                "game_id": "originals.keno",
                "currency": "GOLD",
                "min_bet": 1,
                "max_bet": 100_000,
                # Covers the headline: 100_000·57_551.55 ≈ 5.755e9 ≤ max_win, so even
                # the ultra-rare top HIGH pick-10 jackpot pays in full (uncapped).
                "max_win": 6_000_000_000,
                # Top HIGH pick-10 payout ≈ 57_551.55×. Informational.
                "max_multiplier": Decimal("60000.00000000"),
            },
            {
                "game_id": "originals.roulette",
                "currency": "GOLD",
                "min_bet": 1,
                # CAP-TRAP INVARIANT: green pays ~99× and is COMMON, so max_win MUST be
                # ≥ max_bet·99. 10_000_000·99 = 990_000_000 ≤ max_win — holds; no common
                # green win is ever silently capped (which would depress realized RTP).
                "max_bet": 10_000_000,
                "max_win": 1_000_000_000,
                # Green payout (1-edge)/p = 0.99/0.01 = 99×. Informational.
                "max_multiplier": Decimal("99.00000000"),
            },
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM game_limits WHERE game_id = ANY(:ids)"), {"ids": _GAME_IDS}
    )
    bind.execute(
        sa.text("DELETE FROM game_configs WHERE game_id = ANY(:ids)"), {"ids": _GAME_IDS}
    )
