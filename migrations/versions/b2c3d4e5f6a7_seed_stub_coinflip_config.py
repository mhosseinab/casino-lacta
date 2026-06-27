"""seed stub.coinflip game config + limit

The shared bet loop (S6) needs an authoritative DB ``GameConfig``/``GameLimit``
for its end-to-end fixture game ``stub.coinflip`` (50/50, 1.98x → 0.99 RTP).
Additive + reversible: the downgrade deletes exactly these two rows. Distinct
from the seeded ``originals.dice`` row — no collision.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-27

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAME_ID = "stub.coinflip"


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
                "params": {"win_multiplier": 1.98},
                "rtp": Decimal("0.990000"),
                "volatility": "low",
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
                "max_win": 1_000_000_000,
                "max_multiplier": Decimal("1.98000000"),
            }
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM game_limits WHERE game_id = :g"), {"g": _GAME_ID}
    )
    bind.execute(
        sa.text("DELETE FROM game_configs WHERE game_id = :g"), {"g": _GAME_ID}
    )
