"""Async engine + session-factory construction (app member).

The runtime app and the test-suite talk to Postgres through an asyncpg engine;
Alembic (DDL) runs synchronously elsewhere. ``balance``-bearing reads inside the
ledger use ``SELECT ... FOR UPDATE``, so the pool must comfortably exceed the
number of concurrent money-movement coroutines a test or request fans out.

This is a seam: swapping the persistence target is a URL/factory change here,
never a change in the ledger or bet-loop callers.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str) -> AsyncEngine:
    """Build an async (asyncpg) engine.

    ``pool_size``/``max_overflow`` are sized so a burst of concurrent ledger
    coroutines (e.g. N parallel debits contending on one wallet row) each get a
    connection without starving — the row lock, not the pool, is what serialises
    them.
    """
    return create_async_engine(
        url,
        pool_size=20,
        max_overflow=20,
        pool_pre_ping=True,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory with ``expire_on_commit=False`` so result attributes
    stay readable after the transaction commits (no lazy refresh on a closed
    session)."""
    return async_sessionmaker(engine, expire_on_commit=False)
