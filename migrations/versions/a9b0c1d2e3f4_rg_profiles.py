"""rg: add the responsible-gaming profile table (S38)

S38 turns the S6 allow-everything ``can_bet`` stub into a server-authoritative
pre-bet gate that enforces self-imposed limits, cool-off, and self-exclusion. The
DURABLE policy lives in one additive table, ``rg_profiles`` (one row per user, all
columns nullable so an unconstrained player is the default and never falsely
blocked). The live per-session elapsed timer + reality-check counter live in Redis
(not persisted), so this migration is pure DDL — one ``CREATE TABLE``.

Down: drop exactly this one table, nothing else. Linear chain
(down_revision = b8c9d0e1f2a3).

Revision ID: a9b0c1d2e3f4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rg_profiles",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("spend_limit_minor", sa.BigInteger(), nullable=True),
        sa.Column("loss_limit_minor", sa.BigInteger(), nullable=True),
        sa.Column("session_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("reality_check_seconds", sa.Integer(), nullable=True),
        sa.Column("self_excluded_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cool_off_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_rg_profiles_user_id"),
        sa.PrimaryKeyConstraint("user_id", name="pk_rg_profiles"),
    )


def downgrade() -> None:
    op.drop_table("rg_profiles")
