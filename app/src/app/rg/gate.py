"""Responsible-gaming decision — the server-authoritative pre-debit gate (S38).

S6 shipped an allow-everything stub behind this seam; S38 makes ``can_bet`` enforce
the player's own self-imposed policy BEFORE any ledger movement:

- **self-exclusion** — a standing block (``RgReason.SELF_EXCLUDED``);
- **cool-off** — a temporary timed block (``RgReason.COOL_OFF_ACTIVE``);
- **session-time limit** — too long in one sitting (``RgReason.SESSION_LIMIT_EXCEEDED``);
- **loss limit** — net loss already at/over the cap (``RgReason.LOSS_LIMIT_EXCEEDED``);
- **spend limit** — this wager would push wagered total over the cap
  (``RgReason.SPEND_LIMIT_EXCEEDED``).

A block returns a TYPED reason (``RgReason`` is a ``StrEnum`` so the bet loop's
``RgDenied(str(reason))`` surfaces the bare code, e.g. ``SELF_EXCLUDED``, for the API
and the client to render) — never a generic error. The contract the S6 bet loop
depends on is unchanged: the verdict is still an ``RgDecision(allowed, reason)`` and a
deny still maps to ``RgDenied`` at the call site. (The function is now ``async`` and
takes the session factory so it can read the authoritative state — you cannot enforce
DB/Redis-backed limits from a pure sync stub.)

The DURABLE policy (limits, exclusion, cool-off) lives in Postgres (``RgProfile``);
the live per-session elapsed clock + reality-check counter live in Redis. Spend/loss
are derived read-only by summing the player's settled ``Bet`` rows in the current
session window — no counter is added to the money path. When the player crosses a
reality-check cadence the gate emits a structured ``session.elapsed`` event (stdlib
logging; the real observability sink is S39).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Bet, RgProfile

logger = logging.getLogger("app.rg")

# The load-bearing play-money awareness seam: surfaced to the client so it renders a
# persistent "this is not real money / there are no prizes" notice. GOLD is
# non-redeemable; ``mode=REAL`` is modeled but never wired (no cash-out, no prizes).
NOT_REAL_MONEY: bool = True
PLAY_MONEY_NOTICE: dict[str, object] = {
    "realMoney": False,
    "prizes": False,
    "message": "Play-money only — no real money, no prizes, no cash-out.",
}


def play_money_notice() -> dict[str, object]:
    """The persistent client-facing play-money disclosure (a copy, so callers
    cannot mutate the module constant)."""
    return dict(PLAY_MONEY_NOTICE)


class RgReason(StrEnum):
    """The typed responsible-gaming block reasons surfaced to the UI. A ``StrEnum``
    so ``str(reason)`` is the bare code the API/client renders."""

    SELF_EXCLUDED = "SELF_EXCLUDED"
    COOL_OFF_ACTIVE = "COOL_OFF_ACTIVE"
    SESSION_LIMIT_EXCEEDED = "SESSION_LIMIT_EXCEEDED"
    LOSS_LIMIT_EXCEEDED = "LOSS_LIMIT_EXCEEDED"
    SPEND_LIMIT_EXCEEDED = "SPEND_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class RgDecision:
    """The verdict of the RG gate. ``reason`` is populated only on a deny, with a
    TYPED ``RgReason`` (never a free-form string)."""

    allowed: bool
    reason: RgReason | None = None


_REDIS_URL_DEFAULT = "redis://localhost:6379/0"
_redis_singleton: redis.Redis | None = None

# A sitting ends after this much inactivity: the next gated bet beyond the gap rolls a
# FRESH session (start clock + reality-check high-water mark reset). This is what keeps
# a self-imposed *session-time* limit from hardening into a permanent ban and lets the
# reality-check re-arm each sitting. The same value is the TTL on the session keys, so a
# player who simply walks away has the keys auto-expire (belt to the idle-gap braces).
SESSION_IDLE_GAP_SECONDS = 1800  # 30 minutes


def _default_redis() -> redis.Redis:
    """Lazily build the shared app Redis client (session-timer store). Reused across
    calls; tests inject their own client instead."""
    global _redis_singleton
    if _redis_singleton is None:
        import os

        _redis_singleton = redis.from_url(
            os.environ.get("REDIS_URL", _REDIS_URL_DEFAULT),
            decode_responses=True,
        )
    return _redis_singleton


def _sess_start_key(user_id: str) -> str:
    return f"rg:sess:{user_id}:start"


def _sess_last_key(user_id: str) -> str:
    return f"rg:sess:{user_id}:last"


def _reality_check_key(user_id: str) -> str:
    return f"rg:sess:{user_id}:rc"


async def _resolve_session_start(client: redis.Redis, user_id: str, now: datetime) -> float:
    """The epoch-seconds start of the player's CURRENT session, rolling a fresh session
    over an idle gap so a session timer cannot harden into a permanent block.

    A new session starts (clock reset to ``now``, reality-check high-water mark cleared)
    when there is no recorded session OR the gap since the last gated bet exceeds
    ``SESSION_IDLE_GAP_SECONDS``. Otherwise the existing session continues. Both keys are
    (re)written with a TTL of the idle gap, so a player who walks away has them auto-expire
    — the next bet then starts clean even without the gap arithmetic."""
    start_key = _sess_start_key(user_id)
    last_key = _sess_last_key(user_id)
    now_epoch = now.timestamp()

    start_raw = await client.get(start_key)
    last_raw = await client.get(last_key)
    is_new_session = (
        start_raw is None
        or last_raw is None
        or (now_epoch - float(last_raw)) > SESSION_IDLE_GAP_SECONDS
    )
    if is_new_session:
        start_epoch = now_epoch
        await client.set(start_key, start_epoch, ex=SESSION_IDLE_GAP_SECONDS)
        # Reset the reality-check high-water mark so the new sitting re-arms its reminders.
        await client.delete(_reality_check_key(user_id))
    else:
        start_epoch = float(start_raw)  # type: ignore[arg-type]  # not-None per is_new_session
        # Keep an active session's start alive while the player keeps betting.
        await client.expire(start_key, SESSION_IDLE_GAP_SECONDS)
    # Record this bet as the latest activity (drives the next call's idle-gap test).
    await client.set(last_key, now_epoch, ex=SESSION_IDLE_GAP_SECONDS)
    return start_epoch


async def _emit_reality_check(
    client: redis.Redis,
    *,
    user_id: str,
    elapsed_seconds: float,
    cadence_seconds: int,
) -> None:
    """Emit a ``session.elapsed`` reality-check event each time the player crosses a
    multiple of the cadence (idempotent within a session via a Redis high-water mark)."""
    if cadence_seconds <= 0:
        return
    checkpoint = int(elapsed_seconds // cadence_seconds)
    if checkpoint < 1:
        return
    # High-water mark: emit only on a NEW checkpoint, so the reminder fires once per
    # cadence crossing, not on every bet. (The get-then-set is non-atomic — two
    # concurrent bets crossing the same checkpoint could double-log; that is a harmless
    # duplicate log line, never a money or block effect, so no lock is taken here.)
    key = _reality_check_key(user_id)
    last_raw = await client.get(key)
    last = int(last_raw) if last_raw is not None else 0
    if checkpoint <= last:
        return
    await client.set(key, checkpoint)
    logger.info(
        "session.elapsed",
        extra={
            "event": "session.elapsed",
            "rg_user_id": user_id,
            "elapsed_seconds": int(elapsed_seconds),
            "checkpoint": checkpoint,
            "cadence_seconds": cadence_seconds,
        },
    )


async def _session_spend_loss(
    session: AsyncSession, user_id: str, since: datetime
) -> tuple[int, int]:
    """Read-only (wagered_total, net_loss) for the player's bets since the session
    window started. Net loss = Σ(stake − payout); a winning session is ≤ 0. Derived from
    the ``Bet`` table — never a counter mutated on the money path. An ACTIVE stateful bet
    (NULL payout) counts as a full-stake loss: a deliberate CONSERVATIVE over-count (an
    open round may yet cash out) that only ever blocks SOONER, never later — safe for an
    RG limit."""
    row = await session.execute(
        select(
            func.coalesce(func.sum(Bet.stake_minor), 0),
            func.coalesce(func.sum(Bet.stake_minor - func.coalesce(Bet.payout_minor, 0)), 0),
        ).where(Bet.user_id == user_id, Bet.created_at >= since)
    )
    spend, loss = row.one()
    return int(spend), int(loss)


def _aware(ts: datetime) -> datetime:
    """Treat a naive timestamp (some drivers return naive UTC) as UTC."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


async def can_bet(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    game_id: str,
    stake_minor: int,
    currency: str,
    redis_client: redis.Redis | None = None,
    now: datetime | None = None,
) -> RgDecision:
    """Decide whether ``user_id`` may place this bet, enforcing their self-imposed RG
    policy server-side. Returns ``RgDecision(allowed=True)`` for an unconstrained
    player (no profile / no limits) — the gate never falsely blocks. A constrained
    player who is over a limit / excluded / in cool-off is denied with the matching
    typed ``RgReason``. Emits a ``session.elapsed`` reality-check when the player
    crosses their configured cadence, on the allow path too."""
    now = now or datetime.now(UTC)

    async with session_factory() as session:
        profile = await session.get(RgProfile, user_id)
        if profile is None:
            # Unconstrained player: no policy, no Redis, no false block.
            return RgDecision(allowed=True)

        # Standing / temporary timed blocks (durable, vs the request clock).
        if profile.self_excluded_until is not None and now < _aware(profile.self_excluded_until):
            return RgDecision(allowed=False, reason=RgReason.SELF_EXCLUDED)
        if profile.cool_off_until is not None and now < _aware(profile.cool_off_until):
            return RgDecision(allowed=False, reason=RgReason.COOL_OFF_ACTIVE)

        client = redis_client or _default_redis()
        start_epoch = await _resolve_session_start(client, user_id, now)
        elapsed = max(0.0, now.timestamp() - start_epoch)
        session_start = datetime.fromtimestamp(start_epoch, tz=UTC)

        # Reality-check fires on elapsed time, independent of any block below.
        if profile.reality_check_seconds:
            await _emit_reality_check(
                client,
                user_id=user_id,
                elapsed_seconds=elapsed,
                cadence_seconds=profile.reality_check_seconds,
            )

        if (
            profile.session_limit_seconds is not None
            and elapsed >= profile.session_limit_seconds
        ):
            return RgDecision(allowed=False, reason=RgReason.SESSION_LIMIT_EXCEEDED)

        if profile.spend_limit_minor is not None or profile.loss_limit_minor is not None:
            spend, loss = await _session_spend_loss(session, user_id, session_start)
            if profile.loss_limit_minor is not None and loss >= profile.loss_limit_minor:
                return RgDecision(allowed=False, reason=RgReason.LOSS_LIMIT_EXCEEDED)
            if (
                profile.spend_limit_minor is not None
                and spend + stake_minor > profile.spend_limit_minor
            ):
                return RgDecision(allowed=False, reason=RgReason.SPEND_LIMIT_EXCEEDED)

    return RgDecision(allowed=True)
