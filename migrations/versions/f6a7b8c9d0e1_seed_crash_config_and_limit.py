"""crash: seed the authoritative originals.crash GameConfig + GOLD GameLimit

S19 turns the S18 Crash actor into a money game: players place bets during
WAITING and cash out during RUNNING. The shared crash betting path
(``app.ws.crash_bets``) validates a placed bet against the AUTHORITATIVE runtime
tables exactly like the instant bet loop — so ``originals.crash`` MUST have an
enabled ``GameConfig`` (or every placement fails ``GameDisabled``) and a GOLD
``GameLimit`` (or ``[min_bet, max_bet]`` cannot be enforced). This migration adds
ONLY those two data rows (no DDL).

This is where the S18 ``DEFAULT_CRASH_EDGE = 0.01`` literal becomes authoritative
config: the round's crash point ``C`` is computed by the pure core from this edge
(RTP 0.99 at any cash-out target). The per-round seed + ``C`` + round number are
persisted at round START in the existing server-only ``game_rounds.server_state``
JSONB (already added in S12; never serialized to a client), so NO schema change is
needed — re-settle (S20) re-runs ``CrashRound.open`` from the persisted seed.

GameLimit choices (minor units; ``max_multiplier`` informational — the ENFORCED
ceiling is ``max_win``, applied via ``engine.money.cap`` AFTER the floor). Crash's
``C`` is unbounded (``P(C >= x) = (1-edge)/x``), so an extreme-tail win caps at
``max_win`` by design (a documented product limit; common low-multiplier cash-outs
are uncapped): ``min_bet 1``, ``max_bet 100_000_000``, ``max_win 1_000_000_000``.

Down: delete exactly this one config + one limit row by game_id, nothing else.
Linear chain (down_revision = e5f6a7b8c9d0).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAME_ID = "originals.crash"


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
                # The S18 DEFAULT_CRASH_EDGE literal, now authoritative config.
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
                # The enforced ceiling: C is unbounded, so an extreme-tail cash-out
                # caps here (documented limit). 100_000_000 * 10 = 1e9 = max_win, so
                # any cash-out at <= 10x on a max stake pays in full.
                "max_win": 1_000_000_000,
                # Informational only (max_win is enforced). Crash has no fixed top
                # multiplier; this is a display/audit hint for the common range.
                "max_multiplier": Decimal("1000000.00000000"),
            }
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM game_limits WHERE game_id = :g"), {"g": _GAME_ID})
    bind.execute(sa.text("DELETE FROM game_configs WHERE game_id = :g"), {"g": _GAME_ID})
