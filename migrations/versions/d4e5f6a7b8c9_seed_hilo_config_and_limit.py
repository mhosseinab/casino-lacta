"""hilo: seed originals.hilo config + GOLD limit

S14 wires the second ``StatefulGame`` (HiLo). No schema change is needed — HiLo
reuses the ``game_rounds.server_state`` column added in S12; the in-app SHAPE of
that column is now the saga-level ``{cursor, game}`` fairness envelope (no DDL).

This migration adds only the authoritative runtime rows:

1. ``originals.hilo`` ``GameConfig`` (edge 0.01, no params — the per-step
   multiplier is ``(1-edge)/p(side)``, computed from the shown rank, not config).
2. The GOLD ``GameLimit``. HiLo's cumulative multiplier is UNBOUNDED in principle
   (a long winning streak compounds without limit), so there is no real
   max-multiplier ceiling; the ENFORCED cap is ``max_win`` (payout is floored then
   clamped to ``max_win`` via ``engine.money.cap`` at cashout). ``max_multiplier``
   is informational only.

Down: delete exactly these two rows. Linear chain (down_revision = c3d4e5f6a7b8).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAME_ID = "originals.hilo"


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
                "game_id": _GAME_ID,
                "version": 1,
                "edge": Decimal("0.010000"),
                "params": {},
                "rtp": Decimal("0.990000"),
                "volatility": "high",
                "enabled": True,
            }
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
                "game_id": _GAME_ID,
                "currency": "GOLD",
                "min_bet": 1,
                "max_bet": 100_000_000,
                # The real money ceiling: payout is floored then clamped to max_win,
                # however large the cumulative multiplier grows.
                "max_win": 1_000_000_000,
                # HiLo's cumulative multiplier is unbounded in principle (a streak
                # compounds without limit). Informational only — NOT enforced in the
                # loop; max_win is the enforced cap. (Per-step max is 12.87x.)
                "max_multiplier": Decimal("1000000.00000000"),
            }
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM game_limits WHERE game_id = :g"), {"g": _GAME_ID})
    bind.execute(sa.text("DELETE FROM game_configs WHERE game_id = :g"), {"g": _GAME_ID})
