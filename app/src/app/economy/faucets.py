"""Faucets — the single owner of the welcome grant (S8 seam; S36 extends it).

Today only the welcome grant exists. Auth CALLS :func:`welcome_grant`; it never
inlines its own grant — so there is exactly ONE place that funds a new player and
exactly ONE idempotency key for it. S36 adds the other faucets (daily, hourly,
level-up) to this module and may move the amount/key into config, all WITHOUT auth
changing.

Idempotency is the load-bearing property: the welcome key is ``hash(userId,
'welcome')``, so a re-trigger returns the ORIGINAL grant and books nothing (the
ledger's UNIQUE ``idempotency_key`` constraint is the authority — see
``app.wallet.ledger``). Minor units only; the grant is a balanced two-row
``LedgerEntry`` set whose counterparty is the SYSTEM/house wallet.
"""

from __future__ import annotations

import hashlib

from app.wallet import Ledger, LedgerResult

# The one-time welcome funding for a new play-money guest, in integer minor units
# (e.g. GOLD with 2 decimals → 1_000_000 = 10,000.00 GOLD). Economy POLICY lives
# here, the faucet's domain; S36 may relocate it to DB ``GameConfig`` without auth
# changing. Never a scattered literal at the call sites.
WELCOME_GRANT_MINOR = 1_000_000


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
