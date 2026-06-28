"""Faucets — the economy's coin SOURCES, and the single owner of the welcome grant.

Every faucet funds a player the SAME way: a balanced two-row :class:`LedgerResult`
booked through :meth:`Ledger.grant` (SYSTEM/house counterparty), minor units only,
guarded by a per-window idempotency key so it credits EXACTLY ONCE per eligibility
window. The ledger's UNIQUE ``idempotency_key`` constraint is the dedup AUTHORITY
(see ``app.wallet.ledger``); Redis holds the windows/streak so a too-early claim is
a clean no-op and the daily streak can count.

Faucets and their window keys:

- **welcome** — once EVER, key ``hash(userId, 'welcome')``. Auth CALLS
  :func:`welcome_grant` (it never inlines its own grant), so there is exactly one
  place that funds a new player and exactly one key for it.
- **hourly** — key ``hash(userId, 'hourly', floor(now/3600))``; Redis records the
  last claimed hour-bucket so a re-claim before the hour elapses no-ops.
- **daily streak** — key ``hash(userId, 'daily', floor(now/86400))``; Redis stores
  ``"<day>:<streak>"`` so consecutive days raise the bonus and a gap resets it.
- **level-up** — key ``hash(userId, 'levelup', level)``; once per level. No timer,
  so no Redis — the ledger key alone makes it once-per-level.

Time enters via an injected ``now`` (default :func:`time.time`), so windows advance
deterministically in tests without sleeping. A faucet returns a :class:`FaucetResult`:
``granted`` is ``True`` only when THIS call booked a fresh grant (a replayed ledger
op, i.e. the window was already claimed, reports ``granted=False``).
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis

from app.wallet import Ledger, LedgerResult

# The one-time welcome funding for a new play-money guest, in integer minor units
# (e.g. GOLD with 2 decimals → 1_000_000 = 10,000.00 GOLD). Economy POLICY lives
# here, the faucet's domain; S36 may relocate it to DB ``GameConfig`` without auth
# changing. Never a scattered literal at the call sites.
WELCOME_GRANT_MINOR = 1_000_000

# Recurring faucet policy — amounts in integer minor units, windows in seconds.
HOUR_SECONDS = 3600
DAY_SECONDS = 86_400

#: Flat hourly timer claim.
HOURLY_GRANT_MINOR = 50_000
#: Daily login: a base grant plus a per-day streak step, capped so a long streak
#: cannot run away. ``amount(streak) = BASE + STEP * (min(streak, CAP) - 1)``.
DAILY_BASE_MINOR = 100_000
DAILY_STREAK_STEP_MINOR = 25_000
DAILY_STREAK_CAP = 7
#: Level-up reward — scales linearly with the level reached.
LEVEL_UP_BASE_MINOR = 200_000

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@dataclass(frozen=True)
class FaucetResult:
    """The outcome of one faucet claim.

    ``granted`` is ``True`` ONLY when this call booked a fresh credit; a claim
    inside an already-consumed window returns ``granted=False`` and ``amount_minor=0``.
    ``ledger`` is the booked (or replayed) op, or ``None`` when Redis short-circuited
    the claim before touching the ledger. ``streak`` is meaningful for the daily
    faucet (else ``0``)."""

    granted: bool
    amount_minor: int
    ledger: LedgerResult | None
    streak: int = 0


# --------------------------------------------------------------------------- #
# Redis seam — window/streak state (the "timers/cooldowns in Redis" rule).      #
# A narrow, consumer-shaped string KV (DIP): faucets depend on this protocol,   #
# not on a concrete client. One production impl; the ledger UNIQUE key, not      #
# Redis, is the dedup authority, so no test double is needed.                    #
# --------------------------------------------------------------------------- #
class FaucetStore(Protocol):
    """A string key→value store for per-user faucet windows."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


class RedisFaucetStore:
    """Redis-backed :class:`FaucetStore` (mirrors the WS actors' ``from_url`` seam)."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str = _REDIS_URL) -> RedisFaucetStore:
        return cls(redis.from_url(url))

    async def get(self, key: str) -> str | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def set(self, key: str, value: str) -> None:
        await self._client.set(key, value)

    async def ping(self) -> bool:
        ok: bool = await self._client.ping()
        return ok

    async def aclose(self) -> None:
        await self._client.aclose()


def _redis_hourly_key(user_id: str) -> str:
    return f"faucet:hourly:{user_id}"


def _redis_daily_key(user_id: str) -> str:
    return f"faucet:daily:{user_id}"


def welcome_grant_key(user_id: str) -> str:
    """The welcome grant's idempotency key — ``hash(userId, 'welcome')`` (spec §S8).

    Deterministic per user, so any re-trigger collapses onto the same key and the
    ledger applies the grant exactly once.
    """
    return hashlib.sha256(f"{user_id}:welcome".encode()).hexdigest()


async def welcome_grant(ledger: Ledger, *, user_id: str, wallet_id: str) -> LedgerResult:
    """Fund a new player's wallet once, idempotently.

    A re-trigger for the same ``user_id`` returns the original grant
    (``replayed=True``) and moves no credits. The caller must have already
    committed the ``User`` + ``Wallet`` rows — ``Ledger.grant`` locks the wallet
    row in its own transaction and needs it to exist.
    """
    return await ledger.grant(
        wallet_id=wallet_id,
        amount_minor=WELCOME_GRANT_MINOR,
        idempotency_key=welcome_grant_key(user_id),
        ref=f"welcome:{user_id}",
    )


def _faucet_key(user_id: str, faucet: str, window: int) -> str:
    """Per-(user, faucet, window) idempotency key. User-namespaced so the
    table-wide UNIQUE constraint never collides across players."""
    return hashlib.sha256(f"{user_id}:{faucet}:{window}".encode()).hexdigest()


def hourly_key(user_id: str, now: float) -> str:
    """The hourly faucet's idempotency key for the hour-bucket containing ``now``."""
    return _faucet_key(user_id, "hourly", int(now) // HOUR_SECONDS)


def daily_streak_key(user_id: str, now: float) -> str:
    """The daily faucet's idempotency key for the day-bucket containing ``now``."""
    return _faucet_key(user_id, "daily", int(now) // DAY_SECONDS)


def level_up_key(user_id: str, level: int) -> str:
    """The level-up faucet's idempotency key for ``level`` (once per level, ever)."""
    return _faucet_key(user_id, "levelup", level)


def _daily_amount(streak: int) -> int:
    return DAILY_BASE_MINOR + DAILY_STREAK_STEP_MINOR * (min(streak, DAILY_STREAK_CAP) - 1)


async def hourly_claim(
    ledger: Ledger,
    store: FaucetStore,
    *,
    user_id: str,
    wallet_id: str,
    now: float | None = None,
) -> FaucetResult:
    """Claim the hourly timer faucet for the hour-bucket containing ``now``.

    A claim within an already-consumed hour is a no-op (``granted=False``); once the
    hour elapses a new bucket grants again. The Redis marker gates the fast path; the
    ledger key is the authority (a replayed grant also reports ``granted=False``)."""
    now = time.time() if now is None else now
    bucket = int(now) // HOUR_SECONDS
    last = await store.get(_redis_hourly_key(user_id))
    if last is not None and int(last) >= bucket:
        return FaucetResult(granted=False, amount_minor=0, ledger=None)

    result = await ledger.grant(
        wallet_id=wallet_id,
        amount_minor=HOURLY_GRANT_MINOR,
        idempotency_key=hourly_key(user_id, now),
        ref=f"hourly:{user_id}:{bucket}",
    )
    await store.set(_redis_hourly_key(user_id), str(bucket))
    granted = not result.replayed
    return FaucetResult(
        granted=granted,
        amount_minor=HOURLY_GRANT_MINOR if granted else 0,
        ledger=result,
    )


def _parse_daily(raw: str | None) -> tuple[int | None, int]:
    if raw is None:
        return None, 0
    day_str, streak_str = raw.split(":", 1)
    return int(day_str), int(streak_str)


async def daily_streak_claim(
    ledger: Ledger,
    store: FaucetStore,
    *,
    user_id: str,
    wallet_id: str,
    now: float | None = None,
) -> FaucetResult:
    """Claim the daily login faucet for the day-bucket containing ``now``.

    Consecutive days raise the bonus (streak); a missed day resets it to 1. The
    streak counter lives in Redis (``"<day>:<streak>"``); the per-day ledger key
    enforces once-per-day even if Redis is lost."""
    now = time.time() if now is None else now
    day = int(now) // DAY_SECONDS
    last_day, prev_streak = _parse_daily(await store.get(_redis_daily_key(user_id)))
    if last_day is not None and last_day >= day:
        return FaucetResult(granted=False, amount_minor=0, ledger=None, streak=prev_streak)

    streak = prev_streak + 1 if last_day is not None and day == last_day + 1 else 1
    amount = _daily_amount(streak)
    result = await ledger.grant(
        wallet_id=wallet_id,
        amount_minor=amount,
        idempotency_key=daily_streak_key(user_id, now),
        ref=f"daily:{user_id}:{day}",
    )
    await store.set(_redis_daily_key(user_id), f"{day}:{streak}")
    granted = not result.replayed
    return FaucetResult(
        granted=granted,
        amount_minor=amount if granted else 0,
        ledger=result,
        streak=streak,
    )


async def level_up_claim(
    ledger: Ledger,
    *,
    user_id: str,
    wallet_id: str,
    level: int,
) -> FaucetResult:
    """Reward reaching ``level`` — once per level, ever. Event-driven (not a timer),
    so the ledger key ``hash(userId,'levelup',level)`` alone makes it idempotent; a
    re-trigger for an already-rewarded level reports ``granted=False``."""
    if level <= 0:
        raise ValueError(f"level must be a positive integer, got {level}")
    amount = LEVEL_UP_BASE_MINOR * level
    result = await ledger.grant(
        wallet_id=wallet_id,
        amount_minor=amount,
        idempotency_key=level_up_key(user_id, level),
        ref=f"levelup:{user_id}:{level}",
    )
    granted = not result.replayed
    return FaucetResult(
        granted=granted,
        amount_minor=amount if granted else 0,
        ledger=result,
    )
