"""mines: add game_rounds.server_state + seed originals.mines config/limit

S12 wires the first ``StatefulGame``. Two additive, reversible changes:

1. A SERVER-ONLY ``game_rounds.server_state`` JSONB column holding the opaque round
   state (the Mines mine layout). It is never serialized to a client (redaction), so
   a hidden layout cannot leak mid-round. Nullable — instant rounds leave it NULL.
2. The authoritative ``originals.mines`` ``GameConfig`` (edge 0.01) + GOLD
   ``GameLimit``. Mine count ``M`` is per-bet input, so ``params`` stays empty.

Down: delete exactly these two rows, then drop the column. Linear chain
(down_revision = b2c3d4e5f6a7).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAME_ID = "originals.mines"


def upgrade() -> None:
    op.add_column(
        "game_rounds",
        sa.Column("server_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

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
                # The real money cap: payout is floored then clamped to max_win,
                # regardless of how large the (M,k) multiplier grows.
                "max_win": 1_000_000_000,
                # Theoretical ceiling: max fairMultiplier = C(25,12) = 5_200_300
                # (at M=13,k=12 / M=12,k=13) → ·(1-edge) ≈ 5_148_297. Informational
                # (not enforced in the loop; max_win is the enforced ceiling).
                "max_multiplier": Decimal("5148297.00000000"),
            }
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM game_limits WHERE game_id = :g"), {"g": _GAME_ID})
    bind.execute(sa.text("DELETE FROM game_configs WHERE game_id = :g"), {"g": _GAME_ID})
    op.drop_column("game_rounds", "server_state")
