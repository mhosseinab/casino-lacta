"""Shared test fixtures for the ledger suite.

These tests run against a REAL, alembic-migrated Postgres (not ``create_all``):
the reserved ``SYSTEM`` user + the ``house-GOLD`` wallet — the counterparty of
every double-entry op — exist ONLY because the S2 data migration's
``seed_configs()`` inserts them. Provision the schema before running:

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head

``DATABASE_URL`` defaults to the docker-compose Postgres.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User, Wallet
from app.db.session import make_engine, make_session_factory
from app.wallet import Ledger

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://lacta:lacta@localhost:5432/lacta"
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(DATABASE_URL)
    # Probe the connection at fixture SETUP so a missing/unreachable Postgres yields a
    # clear SKIP ("run alembic upgrade head against a live DB") instead of a misleading
    # mid-test connection FAILURE — the latter has been repeatedly misread as a real
    # seed/assertion regression. CI runs against a live service DB, so nothing skips
    # there; only a genuinely unreachable DB (e.g. a local/worker run with no Postgres)
    # is skipped. Connection-class errors ONLY — any other error still propagates.
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, OperationalError, InterfaceError, DBAPIError) as exc:
        await engine.dispose()
        pytest.skip(
            f"no Postgres reachable at {DATABASE_URL} ({type(exc).__name__}); "
            "DB-dependent test skipped — start Postgres and `alembic upgrade head`."
        )
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def ledger(session_factory: async_sessionmaker[AsyncSession]) -> Ledger:
    return Ledger(session_factory)


@pytest.fixture
async def wallet_id(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """A fresh, zero-balance GOLD/PLAY wallet for an isolated test user.

    Unique ids per test keep tests independent without truncating the shared DB;
    the house wallet's running balance is irrelevant (Σ system stays zero)."""
    wid = f"w-{uuid4().hex}"
    uid = f"u-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(
            Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0)
        )
    return wid
