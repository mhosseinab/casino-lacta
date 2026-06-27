"""ledger idempotency key (DB-level dedup fence)

Adds the nullable ``idempotency_key`` column + a named UNIQUE constraint to
``ledger_entries``. The player-side row of a balanced op carries the op's key;
the house-side row leaves it NULL (Postgres permits multiple NULLs under a
UNIQUE constraint). The constraint is the authority that makes ``debit``/
``credit``/``grant``/``rollback`` apply exactly once, even under a replay race.

Revision ID: a1b2c3d4e5f6
Revises: 68e2a4e66e5a
Create Date: 2026-06-27 21:05:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "68e2a4e66e5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ledger_entries",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_ledger_entries_idempotency_key",
        "ledger_entries",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ledger_entries_idempotency_key",
        "ledger_entries",
        type_="unique",
    )
    op.drop_column("ledger_entries", "idempotency_key")
