"""Leaderboards — Redis sorted sets for the live boards + durable Postgres snapshots.

Three boards, all updated ON SETTLE (the bet/round settlement path supplies a
:class:`Settlement`); each is a Redis sorted set keyed per ``currency``:

- **biggest-win** — the single largest win per user (``ZADD … GT``: a smaller or zero
  later win never lowers it).
- **total-wagered** — running Σ of stakes (``ZINCRBY`` by stake).
- **profit** — running Σ of ``payout - stake`` (``ZINCRBY``; may go negative).

Scores are integer minor units (the iron rule). Redis sorted-set scores are float64,
which represents every integer below 2**53 exactly — play-money totals fit comfortably —
and reads coerce back to ``int``; no float is ever *persisted* (the durable snapshot
stores ints in JSONB).

**Durability.** Redis is the fast, volatile projection; :func:`snapshot` captures the
top-N of each board into the ``leaderboard_snapshots`` Postgres table so a Redis flush
does not lose history. "Periodic" is a SCHEDULING concern of the caller — this module
exposes the callable, not a background loop (KISS/YAGNI).

This module reads settled-bet data and writes a derived ranking projection + a durable
snapshot. It NEVER moves money — credits flow only through the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LeaderboardSnapshot

# Board ids — also the suffix of each Redis key and the ``board`` column value.
BIGGEST_WIN = "biggest_win"
TOTAL_WAGERED = "total_wagered"
PROFIT = "profit"
BOARDS: tuple[str, ...] = (BIGGEST_WIN, TOTAL_WAGERED, PROFIT)

_KEY_PREFIX = "lb"


def board_key(board: str, currency: str = "GOLD") -> str:
    """The Redis sorted-set key for ``board`` in ``currency`` — ``lb:{currency}:{board}``.

    Namespacing by currency keeps minor-unit scores from different currencies from mixing
    on one board (and lets tests isolate with a throwaway currency)."""
    return f"{_KEY_PREFIX}:{currency}:{board}"


@dataclass(frozen=True)
class Settlement:
    """The leaderboard-relevant slice of a settled bet — what the settle path hands in."""

    user_id: str
    stake_minor: int
    payout_minor: int

    @property
    def profit_minor(self) -> int:
        """Net result in minor units (negative on a loss)."""
        return self.payout_minor - self.stake_minor


@dataclass(frozen=True)
class RankEntry:
    """A user's place on a board: their id, integer-minor-unit score, and 1-based rank."""

    user_id: str
    score: int
    rank: int


def _to_int(score: float) -> int:
    """Coerce a Redis float64 score back to its exact integer minor-unit value."""
    return int(round(score))


def _as_str(member: str | bytes) -> str:
    return member.decode() if isinstance(member, bytes) else member


async def record_settlement(
    redis: Redis, settlement: Settlement, *, currency: str = "GOLD"
) -> None:
    """Update all three boards for one settled bet, in a single Redis pipeline.

    Idempotency note: this is an additive projection (``ZINCRBY``), so it must be called
    EXACTLY once per settled bet by the caller — unlike the ledger, the board carries no
    dedup fence. ``biggest-win`` is naturally idempotent under ``GT`` only for the
    high-water value, not for re-adds, so the same single-settle contract applies."""
    pipe = redis.pipeline()
    pipe.zincrby(board_key(TOTAL_WAGERED, currency), settlement.stake_minor, settlement.user_id)
    pipe.zincrby(board_key(PROFIT, currency), settlement.profit_minor, settlement.user_id)
    pipe.zadd(
        board_key(BIGGEST_WIN, currency),
        {settlement.user_id: settlement.payout_minor},
        gt=True,
    )
    await pipe.execute()


async def top(
    redis: Redis, board: str, *, n: int = 10, currency: str = "GOLD"
) -> list[RankEntry]:
    """The top ``n`` of ``board``, highest score first, each with its 1-based rank."""
    if n <= 0:
        return []
    # ``withscores`` → a list of (member, score); redis-py types the response as a broad
    # union, so narrow it to the concrete shape this call returns.
    raw = cast(
        "list[tuple[str | bytes, float]]",
        await redis.zrevrange(board_key(board, currency), 0, n - 1, withscores=True),
    )
    return [
        RankEntry(user_id=_as_str(member), score=_to_int(score), rank=index + 1)
        for index, (member, score) in enumerate(raw)
    ]


async def rank_of(
    redis: Redis, board: str, user_id: str, *, currency: str = "GOLD"
) -> RankEntry | None:
    """A user's place on ``board`` (1-based) + score, or ``None`` if they are not ranked."""
    key = board_key(board, currency)
    rank = cast("int | None", await redis.zrevrank(key, user_id))
    if rank is None:
        return None
    score = await redis.zscore(key, user_id)
    if score is None:  # pragma: no cover - a present rank implies a present score
        return None
    return RankEntry(user_id=user_id, score=_to_int(score), rank=rank + 1)


async def snapshot(
    redis: Redis,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    boards: tuple[str, ...] = BOARDS,
    currency: str = "GOLD",
    top_n: int = 100,
) -> str:
    """Persist a point-in-time top-``top_n`` of each board to Postgres; return the shared
    ``snapshot_id`` (one row per board). The durable backstop behind the volatile Redis
    boards — a Redis flush keeps this history."""
    snapshot_id = uuid4().hex
    captured_at = datetime.now(UTC)
    rows: list[LeaderboardSnapshot] = []
    for board in boards:
        entries = await top(redis, board, n=top_n, currency=currency)
        rows.append(
            LeaderboardSnapshot(
                id=uuid4().hex,
                snapshot_id=snapshot_id,
                board=board,
                currency=currency,
                captured_at=captured_at,
                entries=[
                    {"userId": e.user_id, "score": e.score, "rank": e.rank} for e in entries
                ],
            )
        )
    async with session_factory() as session, session.begin():
        session.add_all(rows)
    return snapshot_id


async def load_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot_id: str,
    board: str,
    *,
    currency: str = "GOLD",
) -> list[RankEntry]:
    """Read back one board's persisted snapshot rows as :class:`RankEntry` list (empty if
    no such snapshot/board)."""
    async with session_factory() as session:
        row = await session.scalar(
            select(LeaderboardSnapshot).where(
                LeaderboardSnapshot.snapshot_id == snapshot_id,
                LeaderboardSnapshot.board == board,
                LeaderboardSnapshot.currency == currency,
            )
        )
    if row is None:
        return []
    return [
        RankEntry(user_id=str(e["userId"]), score=int(e["score"]), rank=int(e["rank"]))
        for e in (row.entries or [])
    ]
