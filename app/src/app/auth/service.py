"""Guest session creation: a new ``User`` + their GOLD/PLAY ``Wallet``, then the
single-owner welcome grant.

Order matters. The ``User`` + ``Wallet`` rows are persisted and COMMITTED first;
only then is the welcome grant invoked — :meth:`Ledger.grant` opens its own
transaction and locks the wallet row ``FOR UPDATE``, so the wallet must already
exist. The grant itself is NOT issued here: this calls
``app.economy.welcome_grant`` (the single owner; S36 will own/extend it), so there
is no duplicate grant logic and the welcome amount/key live in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User, Wallet
from app.economy import welcome_grant
from app.wallet import Ledger

# A guest plays in non-redeemable GOLD on the PLAY-money ledger. (REAL is modeled
# but never wired — CLAUDE.md.) Single source for the current-user dependency too.
GUEST_CURRENCY = "GOLD"
GUEST_MODE = "PLAY"


@dataclass(frozen=True)
class GuestSession:
    """The freshly-created guest identity (no PII — generated ids only)."""

    user_id: str
    wallet_id: str


async def create_guest(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
) -> GuestSession:
    """Create a guest ``User`` + GOLD/PLAY ``Wallet`` and fund it via the welcome seam."""
    user_id = f"guest-{uuid4().hex}"
    wallet_id = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=user_id))
        await session.flush()
        session.add(
            Wallet(
                id=wallet_id,
                user_id=user_id,
                currency=GUEST_CURRENCY,
                mode=GUEST_MODE,
                balance_minor=0,
            )
        )
    # User + Wallet are committed; fund through the single-owner welcome-grant seam.
    await welcome_grant(ledger, user_id=user_id, wallet_id=wallet_id)
    return GuestSession(user_id=user_id, wallet_id=wallet_id)
