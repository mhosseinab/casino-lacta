"""S38 — responsible-gaming gate, end to end against real Postgres + Redis.

The S6 ``can_bet`` allowed everything; S38 enforces the player's own self-imposed
policy at the pre-debit gate and emits reality-check events. These tests bind:

- a self-EXCLUDED player is blocked with ``RgReason.SELF_EXCLUDED``;
- a player in COOL-OFF is blocked while the window is active and ALLOWED once it
  elapses (two calls, different clocks — no sleeping);
- a player over their SESSION-time limit is blocked with ``SESSION_LIMIT_EXCEEDED``;
- a player over their LOSS limit / about to exceed their SPEND limit is blocked with
  the matching typed reason;
- a ``session.elapsed`` reality-check event is emitted when the cadence is crossed;
- a within-limits player (and a player with no profile at all) is NOT falsely blocked;
- the "not real money / no prizes" flag is exposed as a constant.

Typed reasons (never a generic error) are the contract: each assert checks the
SPECIFIC ``RgReason``, not merely "blocked". Requires the migrated DB + Redis:

    docker compose up -d postgres redis
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Bet, GameRound, RgProfile, User, Wallet
from app.rg import NOT_REAL_MONEY, RgReason, can_bet, play_money_notice

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis_client() -> AsyncIterator[redis_async.Redis]:
    client: redis_async.Redis = redis_async.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except (RedisError, OSError) as exc:  # pragma: no cover - infra skip
        await client.aclose()
        pytest.skip(f"no Redis at {REDIS_URL} ({type(exc).__name__}); RG test skipped.")
    yield client
    await client.aclose()


async def _make_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    """A fresh user + zero-balance GOLD/PLAY wallet; returns (user_id, wallet_id)."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
    return uid, wid


async def _set_profile(
    session_factory: async_sessionmaker[AsyncSession], user_id: str, **fields: object
) -> None:
    async with session_factory() as session, session.begin():
        session.add(RgProfile(user_id=user_id, **fields))


async def _add_settled_bet(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    wallet_id: str,
    stake_minor: int,
    payout_minor: int,
    created_at: datetime,
) -> None:
    """Insert a SETTLED round+bet so spend/loss sums have something to read."""
    rid = uuid4().hex
    async with session_factory() as session, session.begin():
        session.add(
            GameRound(
                id=rid,
                game_id="originals.dice",
                type="SINGLE",
                status="SETTLED",
                created_at=created_at,
                settled_at=created_at,
            )
        )
        await session.flush()
        session.add(
            Bet(
                id=rid,
                round_id=rid,
                user_id=user_id,
                wallet_id=wallet_id,
                currency="GOLD",
                mode="PLAY",
                stake_minor=stake_minor,
                status="WON" if payout_minor else "LOST",
                payout_minor=payout_minor,
                created_at=created_at,
                settled_at=created_at,
            )
        )


async def _seed_session_start(
    client: redis_async.Redis,
    user_id: str,
    start: datetime,
    last: datetime | None = None,
) -> None:
    """Seed an in-progress session: a start clock and a last-activity marker (defaults
    to ``start``). The gate rolls a fresh session only when the gap since ``last``
    exceeds the idle threshold, so seeding both pins the window for a deterministic test."""
    await client.set(f"rg:sess:{user_id}:start", start.timestamp())
    await client.set(f"rg:sess:{user_id}:last", (last or start).timestamp())


# --------------------------------------------------------------------------- #
# The play-money awareness flag (a constant the client renders persistently).
# --------------------------------------------------------------------------- #
def test_not_real_money_flag_exposed() -> None:
    assert NOT_REAL_MONEY is True
    notice = play_money_notice()
    assert notice["realMoney"] is False
    assert notice["prizes"] is False


# --------------------------------------------------------------------------- #
# No profile / within limits → never a false block.
# --------------------------------------------------------------------------- #
async def test_no_profile_is_allowed(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, _ = await _make_user(session_factory)
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
    )
    assert decision.allowed is True
    assert decision.reason is None


async def test_within_spend_limit_is_allowed(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, wallet_id = await _make_user(session_factory)
    now = datetime.now(UTC)
    await _seed_session_start(redis_client, user_id, now - timedelta(seconds=30))
    await _set_profile(session_factory, user_id, spend_limit_minor=1000)
    await _add_settled_bet(
        session_factory,
        user_id=user_id,
        wallet_id=wallet_id,
        stake_minor=900,
        payout_minor=0,
        created_at=now - timedelta(seconds=10),
    )
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=50,  # 900 + 50 = 950 <= 1000
        currency="GOLD",
        redis_client=redis_client,
        now=now,
    )
    assert decision.allowed is True


# --------------------------------------------------------------------------- #
# Typed blocks — each asserts the SPECIFIC reason, not merely "blocked".
# --------------------------------------------------------------------------- #
async def test_self_excluded_is_blocked(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, _ = await _make_user(session_factory)
    now = datetime.now(UTC)
    await _set_profile(
        session_factory, user_id, self_excluded_until=now + timedelta(days=1)
    )
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now,
    )
    assert decision.allowed is False
    assert decision.reason is RgReason.SELF_EXCLUDED


async def test_cool_off_blocks_while_active_then_allows(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, _ = await _make_user(session_factory)
    now = datetime.now(UTC)
    await _set_profile(
        session_factory, user_id, cool_off_until=now + timedelta(hours=1)
    )
    blocked = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now,
    )
    assert blocked.allowed is False
    assert blocked.reason is RgReason.COOL_OFF_ACTIVE

    # Same player, clock advanced past the cool-off window → allowed (no sleeping).
    allowed = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now + timedelta(hours=2),
    )
    assert allowed.allowed is True
    assert allowed.reason is None


async def test_session_time_limit_is_blocked(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, _ = await _make_user(session_factory)
    now = datetime.now(UTC)
    await _seed_session_start(redis_client, user_id, now - timedelta(seconds=120))
    await _set_profile(session_factory, user_id, session_limit_seconds=60)
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now,
    )
    assert decision.allowed is False
    assert decision.reason is RgReason.SESSION_LIMIT_EXCEEDED


async def test_loss_limit_is_blocked(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, wallet_id = await _make_user(session_factory)
    now = datetime.now(UTC)
    await _seed_session_start(redis_client, user_id, now - timedelta(seconds=30))
    await _set_profile(session_factory, user_id, loss_limit_minor=500)
    # Net loss 600 (staked 1000, won back 400) — already over the 500 cap.
    await _add_settled_bet(
        session_factory,
        user_id=user_id,
        wallet_id=wallet_id,
        stake_minor=1000,
        payout_minor=400,
        created_at=now - timedelta(seconds=10),
    )
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now,
    )
    assert decision.allowed is False
    assert decision.reason is RgReason.LOSS_LIMIT_EXCEEDED


async def test_spend_limit_is_blocked(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    user_id, wallet_id = await _make_user(session_factory)
    now = datetime.now(UTC)
    await _seed_session_start(redis_client, user_id, now - timedelta(seconds=30))
    await _set_profile(session_factory, user_id, spend_limit_minor=1000)
    await _add_settled_bet(
        session_factory,
        user_id=user_id,
        wallet_id=wallet_id,
        stake_minor=900,
        payout_minor=0,
        created_at=now - timedelta(seconds=10),
    )
    decision = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=200,  # 900 + 200 = 1100 > 1000
        currency="GOLD",
        redis_client=redis_client,
        now=now,
    )
    assert decision.allowed is False
    assert decision.reason is RgReason.SPEND_LIMIT_EXCEEDED


# --------------------------------------------------------------------------- #
# Reality-check: a structured ``session.elapsed`` event at the cadence.
# --------------------------------------------------------------------------- #
async def test_reality_check_event_emitted(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis_async.Redis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id, _ = await _make_user(session_factory)
    now = datetime.now(UTC)
    # 61s into the session with a 60s cadence → one checkpoint crossed.
    await _seed_session_start(redis_client, user_id, now - timedelta(seconds=61))
    await _set_profile(session_factory, user_id, reality_check_seconds=60)
    with caplog.at_level(logging.INFO, logger="app.rg"):
        decision = await can_bet(
            session_factory,
            user_id=user_id,
            game_id="originals.dice",
            stake_minor=100,
            currency="GOLD",
            redis_client=redis_client,
            now=now,
        )
    assert decision.allowed is True  # reality-check does not block
    events = [r for r in caplog.records if r.getMessage() == "session.elapsed"]
    assert len(events) == 1
    record = events[0]
    assert record.checkpoint == 1  # type: ignore[attr-defined]  # set via logging `extra`
    assert record.rg_user_id == user_id  # type: ignore[attr-defined]


async def test_reality_check_fires_once_per_cadence_crossing(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis_async.Redis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Binds the IDEMPOTENCY property the single-call test cannot: within one cadence
    window only ONE event fires across repeated bets, and the NEXT window fires again.
    Deleting the high-water-mark guard in ``_emit_reality_check`` makes this fail."""
    user_id, _ = await _make_user(session_factory)
    start = datetime.now(UTC)
    await _seed_session_start(redis_client, user_id, start)
    await _set_profile(session_factory, user_id, reality_check_seconds=60)

    def _count(records: list[logging.LogRecord]) -> int:
        return len([r for r in records if r.getMessage() == "session.elapsed"])

    # Two bets both inside checkpoint 1 (61s, then 90s elapsed) → exactly ONE event.
    with caplog.at_level(logging.INFO, logger="app.rg"):
        await can_bet(
            session_factory,
            user_id=user_id,
            game_id="originals.dice",
            stake_minor=100,
            currency="GOLD",
            redis_client=redis_client,
            now=start + timedelta(seconds=61),
        )
        await can_bet(
            session_factory,
            user_id=user_id,
            game_id="originals.dice",
            stake_minor=100,
            currency="GOLD",
            redis_client=redis_client,
            now=start + timedelta(seconds=90),
        )
    assert _count(caplog.records) == 1  # not two — the second bet is suppressed

    # Cross into checkpoint 2 (121s elapsed) → a SECOND event fires.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.rg"):
        await can_bet(
            session_factory,
            user_id=user_id,
            game_id="originals.dice",
            stake_minor=100,
            currency="GOLD",
            redis_client=redis_client,
            now=start + timedelta(seconds=121),
        )
    second = [r for r in caplog.records if r.getMessage() == "session.elapsed"]
    assert len(second) == 1
    assert second[0].checkpoint == 2  # type: ignore[attr-defined]


async def test_session_rolls_over_after_idle_gap(
    session_factory: async_sessionmaker[AsyncSession], redis_client: redis_async.Redis
) -> None:
    """A self-imposed SESSION-time limit must not become a permanent ban: after an idle
    gap the session clock resets and a previously session-limited player can bet again."""
    from app.rg.gate import SESSION_IDLE_GAP_SECONDS

    user_id, _ = await _make_user(session_factory)
    now1 = datetime.now(UTC)
    # An in-progress, 120s-deep session (recent activity → no rollover yet).
    await _seed_session_start(
        redis_client,
        user_id,
        start=now1 - timedelta(seconds=120),
        last=now1 - timedelta(seconds=1),
    )
    await _set_profile(session_factory, user_id, session_limit_seconds=60)

    blocked = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now1,
    )
    assert blocked.allowed is False
    assert blocked.reason is RgReason.SESSION_LIMIT_EXCEEDED

    # Player walks away past the idle gap, then returns → fresh session, elapsed ~0.
    now2 = now1 + timedelta(seconds=SESSION_IDLE_GAP_SECONDS + 10)
    allowed = await can_bet(
        session_factory,
        user_id=user_id,
        game_id="originals.dice",
        stake_minor=100,
        currency="GOLD",
        redis_client=redis_client,
        now=now2,
    )
    assert allowed.allowed is True
    assert allowed.reason is None
    # The Redis session clock genuinely reset to the new sitting's start.
    new_start = float(await redis_client.get(f"rg:sess:{user_id}:start"))
    assert abs(new_start - now2.timestamp()) < 1.0
