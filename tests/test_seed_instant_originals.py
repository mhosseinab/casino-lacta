"""Playability proof for the five seeded instant Originals (Limbo, Pocket Dice,
Plinko, Keno, Roulette).

These games have engine impls + ``engine.registry`` defaults but, before this
migration, no authoritative DB ``GameConfig``/``GameLimit`` rows — so the shared
bet loop rejects them (``GameDisabled`` / no limit) and they are not API-playable.

This suite is the "demoable" proof: against a real, alembic-migrated Postgres,
each game now flows through ``app.games.place_bet`` to a SETTLED ``BetObject``
(status WON/LOST), booking exactly one ``WAGER`` (and one ``WIN`` only on a win),
with the wallet reconciling to the cent. It also pins that the seeded ``params``
for the data-driven games (Plinko, Keno, Roulette pockets) equal the reviewed
``engine.registry.default_config`` values — the single source of truth — so the
migration cannot drift from the engine.

RED before the migration (head ``d4e5f6a7b8c9``): every game raises
``GameDisabled``. GREEN after: all five settle.

Requires the migrated DB (see ``tests/conftest.py``):

    docker run ... postgres:16   # or docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import GameConfig, LedgerEntry, User, Wallet
from app.games import place_bet
from app.wallet import Ledger
from engine.registry import default_config

# (game_id, a valid per-game ``input``) — each input passes the game's own
# ``validate_input`` fence so ``place_bet`` settles rather than 4xx-ing.
GAMES: list[tuple[str, dict[str, Any]]] = [
    ("originals.limbo", {"target": 1.01}),
    ("originals.pocketdice", {"target": 7, "direction": "UNDER"}),
    ("originals.plinko", {"rows": 8, "risk": "LOW"}),
    ("originals.keno", {"picks": [1, 2, 3], "risk": "LOW"}),
    ("originals.roulette", {"bets": [{"value": "RED", "stakeMinor": 100}]}),
]

# Games whose params are data-driven tables/maps sourced from the registry default.
PARAM_DRIVEN = ["originals.plinko", "originals.keno", "originals.roulette"]

_GRANT = 1_000_000
_STAKE = 100


async def _seed_player(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> tuple[str, str]:
    """A funded GOLD/PLAY player. The bet loop lazily provisions the server/client
    seed + nonce counter on first bet, so only the user + wallet + grant are needed."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        await session.flush()  # the User must exist before its FK-dependent wallet
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
    await ledger.grant(wallet_id=wid, amount_minor=_GRANT, idempotency_key=f"{wid}:seed-grant")
    return uid, wid


async def _ledger_type_count(
    session_factory: async_sessionmaker[AsyncSession], wallet_id: str, type_: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.wallet_id == wallet_id, LedgerEntry.type == type_)
        )
    return int(n or 0)


@pytest.mark.parametrize("game_id, bet_input", GAMES)
async def test_instant_original_is_api_playable(
    ledger: Ledger,
    session_factory: async_sessionmaker[AsyncSession],
    game_id: str,
    bet_input: dict[str, Any],
) -> None:
    """Each seeded instant Original settles through the shared bet loop: a single
    debit, a single credit only on a win, and a reconciled wallet to the cent."""
    uid, wid = await _seed_player(session_factory, ledger)
    bet_id = f"bet-{uuid4().hex}"

    bet = await place_bet(
        session_factory,
        ledger,
        user_id=uid,
        game_id=game_id,
        bet_id=bet_id,
        stake_minor=_STAKE,
        input=bet_input,
    )

    # Settled atomically (instant) — never left ACTIVE.
    assert bet.status in ("WON", "LOST"), bet.status
    payout = bet.outcome["payoutMinor"]
    assert payout >= 0
    won = bet.status == "WON"
    assert (payout > 0) == won

    # Single debit; single credit ONLY on a win.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == (1 if won else 0)
    # Balanced wallet: grant - stake + payout, reconciled to the cent.
    assert await ledger.reconcile(wid) == _GRANT - _STAKE + payout


@pytest.mark.parametrize("game_id", PARAM_DRIVEN)
async def test_seeded_params_match_registry_default(
    session_factory: async_sessionmaker[AsyncSession], game_id: str
) -> None:
    """The data-driven configs (Plinko/Keno tables, Roulette pockets) are seeded
    from ``engine.registry.default_config`` — assert the persisted DB row equals
    the reviewed engine default so the migration cannot silently drift."""
    async with session_factory() as session:
        params = await session.scalar(
            select(GameConfig.params).where(
                GameConfig.game_id == game_id, GameConfig.enabled.is_(True)
            )
        )
    assert params == default_config(game_id).params
