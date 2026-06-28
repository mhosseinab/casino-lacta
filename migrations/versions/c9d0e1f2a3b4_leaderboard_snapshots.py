"""leaderboards: durable point-in-time snapshot table

S37 backs the volatile Redis sorted-set boards (biggest-win, total-wagered, profit) with
a durable Postgres snapshot so a Redis flush does not lose history. One additive, fully
reversible change: create ``leaderboard_snapshots``. Rows captured together share a
``snapshot_id`` (one per board); ``entries`` is the ranked top-N JSONB
(``[{userId, score, rank}]``) with integer-minor-unit scores.

Down: drop the index then the table. Linear chain (down_revision = b8c9d0e1f2a3).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "leaderboard_snapshots"
_INDEX = "ix_leaderboard_snapshots_snapshot_id"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("board", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("entries", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(_INDEX, _TABLE, ["snapshot_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
