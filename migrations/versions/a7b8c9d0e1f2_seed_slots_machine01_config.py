"""slots: seed the authoritative slots.machine01 GameConfig + GOLD GameLimit

S23 ships the first data-driven slot machine. The shared bet loop validates a spin
against the AUTHORITATIVE runtime tables exactly like every other game, so
``slots.machine01`` MUST have an enabled ``GameConfig`` (or every spin fails
``GameDisabled``) and a GOLD ``GameLimit`` (or ``[min_bet, max_bet]`` cannot be
enforced). This migration adds ONLY those two data rows (no DDL).

**JSON is the single source of truth.** Engine purity forbids the engine reading a
file, so the machine's strips/paytable/paylines/feature live in
``engine/src/engine/slots/machines/machine01.json`` as INERT DATA and the registry
default carries NO params. The authoritative ``GameConfig.params`` is therefore
SOURCED HERE, at migration time, from that JSON via the app-side reader
(``app.slots.load_machine``) — the same reader the RTP gate uses — so the seed can
never drift from the machine definition (no Python-literal copy). ``edge``/``rtp``/
``volatility`` come from the same JSON. (S24 adds machine02/03 the same way — "config
+ art, not code".)

GameLimit choices (minor units; ``max_multiplier`` informational — the ENFORCED
ceiling is ``max_win``, applied via ``engine.money.cap`` AFTER the floor). The top
single-spin multiplier is ≈ 607× (ten W×5 lines ≈ 357× plus a fully-retriggered
free-spins run ≈ 250×); ``max_bet 100_000`` → ``100_000 · 607 ≈ 6.1e7 ≤ max_win
1_000_000_000``, so NO win — not even the headline jackpot — is ever silently capped
(which would depress realized RTP below the gated target).

Down: delete exactly this one config + one limit row by game_id, nothing else.
Linear chain (down_revision = f6a7b8c9d0e1).

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.slots import load_machine

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAME_ID = "slots.machine01"


def upgrade() -> None:
    machine = load_machine(_GAME_ID)

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
                # Informational for slots (RTP emerges from the strips/paytable, not
                # ``edge``); sourced from the JSON for a consistent audit record.
                "edge": Decimal(str(machine.edge)),
                # The strips/paytable/paylines/feature — the SOLE runtime authority,
                # sourced from machine01.json (no drift from a Python literal).
                "params": machine.params,
                "rtp": Decimal(str(machine.rtp)),
                "volatility": machine.volatility,
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
                "max_bet": 100_000,
                # 100_000 · 607 ≈ 6.1e7 ≤ max_win → even the headline jackpot is uncapped.
                "max_win": 1_000_000_000,
                # Top single-spin multiplier ≈ 607×. Informational (max_win is enforced).
                "max_multiplier": Decimal("607.00000000"),
            }
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM game_limits WHERE game_id = :g"), {"g": _GAME_ID})
    bind.execute(sa.text("DELETE FROM game_configs WHERE game_id = :g"), {"g": _GAME_ID})
