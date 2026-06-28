"""slots: seed the authoritative machine02 (low vol) + machine03 (high vol) configs + limits

S24 ships two more data-driven slot machines, each "config + art, not code": both reuse
the SAME ``engine.slots.framework`` GAME (no per-machine engine code). The shared bet loop
validates a spin against the AUTHORITATIVE runtime tables exactly like every other game, so
each machine MUST have an enabled ``GameConfig`` (or every spin fails ``GameDisabled``) and
a GOLD ``GameLimit`` (or ``[min_bet, max_bet]`` cannot be enforced). This migration adds
ONLY those data rows (no DDL).

**JSON is the single source of truth.** Engine purity forbids the engine reading a file, so
each machine's strips/paytable/paylines/feature live in
``engine/src/engine/slots/machines/machine0{2,3}.json`` as INERT DATA and the registry
default carries NO params. The authoritative ``GameConfig.params`` is therefore SOURCED
HERE, at migration time, from that JSON via the app-side reader (``app.slots.load_machine``)
— the SAME reader the RTP gate uses — so the seed can never drift from the machine
definition (no Python-literal copy). ``edge``/``rtp``/``volatility`` come from the same JSON.

GameLimit choices (minor units; ``max_multiplier`` informational — the ENFORCED ceiling is
``max_win``, applied via ``engine.money.cap`` AFTER the floor). Top single-spin multipliers
(line jackpot + a fully-retriggered free-spins run, ``maxSpins × max prize``):

* machine02 ≈ 375× → ``max_bet 100_000 · 375 ≈ 3.8e7 ≤ max_win 1e9`` (uncapped).
* machine03 ≈ 5_709× → ``max_bet 100_000 · 5_709 ≈ 5.7e8 ≤ max_win 1e9`` (uncapped).

So no win — not even the headline jackpot — is ever silently capped (which would depress
realized RTP below the gated target). ``max_multiplier`` is set above each computed top.

Down: delete exactly these two config + two limit rows by game_id, nothing else.
Linear chain (down_revision = a7b8c9d0e1f2).

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.slots import load_machine

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# game_id -> informational max single-spin multiplier (max_win is the ENFORCED ceiling).
_MACHINES: dict[str, Decimal] = {
    "slots.machine02": Decimal("400.00000000"),
    "slots.machine03": Decimal("6000.00000000"),
}


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
    game_limits = sa.table(
        "game_limits",
        sa.column("game_id", sa.String),
        sa.column("currency", sa.String),
        sa.column("min_bet", sa.BigInteger),
        sa.column("max_bet", sa.BigInteger),
        sa.column("max_win", sa.BigInteger),
        sa.column("max_multiplier", sa.Numeric),
    )
    for game_id, max_mult in _MACHINES.items():
        machine = load_machine(game_id)
        op.bulk_insert(
            game_configs,
            [
                {
                    "game_id": game_id,
                    "version": 1,
                    # Informational for slots (RTP emerges from the strips/paytable, not
                    # ``edge``); sourced from the JSON for a consistent audit record.
                    "edge": Decimal(str(machine.edge)),
                    # The strips/paytable/paylines/feature — the SOLE runtime authority,
                    # sourced from the JSON (no drift from a Python literal).
                    "params": machine.params,
                    "rtp": Decimal(str(machine.rtp)),
                    "volatility": machine.volatility,
                    "enabled": True,
                }
            ],
        )
        op.bulk_insert(
            game_limits,
            [
                {
                    "game_id": game_id,
                    "currency": "GOLD",
                    "min_bet": 1,
                    "max_bet": 100_000,
                    # max_bet · top_multiplier ≤ max_win → even the jackpot is uncapped.
                    "max_win": 1_000_000_000,
                    "max_multiplier": max_mult,
                }
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    for game_id in _MACHINES:
        bind.execute(sa.text("DELETE FROM game_limits WHERE game_id = :g"), {"g": game_id})
        bind.execute(sa.text("DELETE FROM game_configs WHERE game_id = :g"), {"g": game_id})
