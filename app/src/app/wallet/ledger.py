"""Append-only, double-entry coin ledger — the single owner of money movement.

Iron rules enforced here (CLAUDE.md):

- **Double-entry only.** ``debit``/``credit``/``grant``/``rollback`` each write a
  BALANCED two-row :class:`~app.db.models.LedgerEntry` set sharing one ``txn_id``:
  the player wallet row (signed delta) + the SYSTEM/house wallet row (the exact
  negation), so the pair sums to zero and credits never enter or leave the system
  without their house counterparty. System-wide ``Σ(all entries) == 0`` holds by
  construction.
- **Projection, never mutated directly.** ``wallet.balance_minor`` is updated only
  inside the same transaction that appends its ledger rows; :meth:`Ledger.reconcile`
  proves ``balance == Σ(entries)`` per wallet.
- **Atomicity.** Both ledger rows + both wallet projections commit in ONE
  transaction; any failure rolls back all of it.
- **Idempotency (DB-level).** The player-side row carries the op's
  ``idempotency_key`` under a UNIQUE constraint. A pre-read serves the fast path;
  the UNIQUE constraint is the *authority* — a racing replay that slips past the
  pre-read fails the insert (``IntegrityError``) and is served the ORIGINAL result,
  so an op applies exactly once even under concurrency.
- **No overspend.** The player wallet row is locked ``FOR UPDATE`` before its
  balance is read, so N concurrent debits serialise and cannot drive the balance
  negative; insufficient funds is a typed error.
- **Minor units only.** Every amount/delta/balance is an ``int`` of minor units.

Money math (rounding/caps) lives in ``engine.money``; this module only moves
already-computed integer amounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import SYSTEM_USER_ID, LedgerEntry, Wallet


class LedgerError(Exception):
    """Base class for ledger-domain failures."""


class InsufficientFunds(LedgerError):
    """A debit/rollback would drive the wallet balance below zero."""

    def __init__(self, wallet_id: str, balance_minor: int, requested_minor: int) -> None:
        super().__init__(
            f"wallet {wallet_id}: balance {balance_minor} cannot cover {requested_minor}"
        )
        self.wallet_id = wallet_id
        self.balance_minor = balance_minor
        self.requested_minor = requested_minor


class WalletNotFound(LedgerError):
    """No wallet (player or house) for the given id/currency."""


class UnknownOp(LedgerError):
    """``rollback`` was given an ``op_ref`` with no prior balanced op."""


class ReconcileError(LedgerError):
    """A wallet's projected balance disagrees with the sum of its ledger rows."""


@dataclass(frozen=True)
class LedgerResult:
    """The outcome of one balanced op, reconstructable from the player-side row."""

    txn_id: str
    wallet_id: str
    balance_after: int
    delta_minor: int
    replayed: bool


def _result(entry: LedgerEntry, *, replayed: bool) -> LedgerResult:
    return LedgerResult(
        txn_id=entry.txn_id,
        wallet_id=entry.wallet_id,
        balance_after=entry.balance_after,
        delta_minor=entry.delta_minor,
        replayed=replayed,
    )


class Ledger:
    """Double-entry ledger bound to an async session factory (a DIP seam — the
    persistence target is injected, the ledger logic is transport-agnostic)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def debit(
        self, *, wallet_id: str, amount_minor: int, idempotency_key: str, ref: str | None = None
    ) -> LedgerResult:
        """Move ``amount_minor`` FROM the player TO the house (a WAGER). Fails with
        :class:`InsufficientFunds` if the wallet cannot cover it."""
        _require_positive(amount_minor)
        return await self._book(
            wallet_id=wallet_id,
            player_delta=-amount_minor,
            type_="WAGER",
            idempotency_key=idempotency_key,
            ref=ref,
        )

    async def credit(
        self, *, wallet_id: str, amount_minor: int, idempotency_key: str, ref: str | None = None
    ) -> LedgerResult:
        """Move ``amount_minor`` FROM the house TO the player (a WIN)."""
        _require_positive(amount_minor)
        return await self._book(
            wallet_id=wallet_id,
            player_delta=amount_minor,
            type_="WIN",
            idempotency_key=idempotency_key,
            ref=ref,
        )

    async def grant(
        self, *, wallet_id: str, amount_minor: int, idempotency_key: str, ref: str | None = None
    ) -> LedgerResult:
        """Fund the player from the house (a faucet/economy GRANT)."""
        _require_positive(amount_minor)
        return await self._book(
            wallet_id=wallet_id,
            player_delta=amount_minor,
            type_="GRANT",
            idempotency_key=idempotency_key,
            ref=ref,
        )

    async def rollback(
        self, *, op_ref: str, idempotency_key: str | None = None, ref: str | None = None
    ) -> LedgerResult:
        """Reverse a prior op (identified by its ``txn_id``) by booking the inverse
        balanced pair. Append-only: nothing is deleted. Idempotent on its own key."""
        async with self._sf() as session:
            original = await session.scalar(
                select(LedgerEntry).where(
                    LedgerEntry.txn_id == op_ref,
                    LedgerEntry.idempotency_key.is_not(None),
                )
            )
        if original is None:
            raise UnknownOp(f"no op to roll back for txn_id {op_ref}")
        return await self._book(
            wallet_id=original.wallet_id,
            player_delta=-original.delta_minor,
            type_="ROLLBACK",
            idempotency_key=idempotency_key or f"rollback:{op_ref}",
            ref=ref or f"rollback:{op_ref}",
        )

    async def reconcile(self, wallet_id: str) -> int:
        """Assert ``balance_minor == Σ(delta_minor)`` for the wallet; return the
        reconciled balance. Raises :class:`ReconcileError` on any disagreement."""
        async with self._sf() as session:
            wallet = await session.get(Wallet, wallet_id)
            if wallet is None:
                raise WalletNotFound(f"wallet {wallet_id} not found")
            entries_sum = await session.scalar(
                select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
                    LedgerEntry.wallet_id == wallet_id
                )
            )
        total = int(entries_sum or 0)
        if wallet.balance_minor != total:
            raise ReconcileError(
                f"wallet {wallet_id}: projection {wallet.balance_minor} != Σ entries {total}"
            )
        return total

    async def system_total(self) -> int:
        """``Σ(delta_minor)`` across EVERY ledger row — must be ``0`` (closed
        double-entry system: every op books a player delta and its house negation)."""
        async with self._sf() as session:
            entries_sum = await session.scalar(
                select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0))
            )
        return int(entries_sum or 0)

    async def _book(
        self,
        *,
        wallet_id: str,
        player_delta: int,
        type_: str,
        idempotency_key: str,
        ref: str | None,
    ) -> LedgerResult:
        """The atomic core: one transaction, one balanced two-row set, both
        projections updated. The UNIQUE ``idempotency_key`` is the dedup authority."""
        try:
            async with self._sf() as session, session.begin():
                # Fast path: an already-applied key returns the original, books nothing.
                existing = await self._find(session, idempotency_key)
                if existing is not None:
                    return _result(existing, replayed=True)

                # Lock the player wallet row FIRST (consistent order → deadlock-free),
                # so its balance is read under an exclusive row lock — no overspend race.
                wallet = await session.get(Wallet, wallet_id, with_for_update=True)
                if wallet is None:
                    raise WalletNotFound(f"wallet {wallet_id} not found")

                new_player_balance = wallet.balance_minor + player_delta
                if new_player_balance < 0:
                    raise InsufficientFunds(wallet_id, wallet.balance_minor, -player_delta)

                house = await self._house_wallet(session, wallet.currency, wallet.mode)
                new_house_balance = house.balance_minor - player_delta  # exact negation
                txn_id = uuid4().hex

                session.add_all(
                    [
                        LedgerEntry(
                            id=uuid4().hex,
                            wallet_id=wallet.id,
                            txn_id=txn_id,
                            type=type_,
                            delta_minor=player_delta,
                            balance_after=new_player_balance,
                            ref=ref,
                            idempotency_key=idempotency_key,  # player row owns the fence
                        ),
                        LedgerEntry(
                            id=uuid4().hex,
                            wallet_id=house.id,
                            txn_id=txn_id,
                            type=type_,
                            delta_minor=-player_delta,
                            balance_after=new_house_balance,
                            ref=ref,
                            idempotency_key=None,  # house row stays NULL (UNIQUE allows it)
                        ),
                    ]
                )
                wallet.balance_minor = new_player_balance
                house.balance_minor = new_house_balance

                return LedgerResult(
                    txn_id=txn_id,
                    wallet_id=wallet.id,
                    balance_after=new_player_balance,
                    delta_minor=player_delta,
                    replayed=False,
                )
        except IntegrityError:
            # A racing replay slipped past the pre-read and lost the UNIQUE race:
            # the whole transaction rolled back (nothing double-applied). Serve the
            # ORIGINAL result — this is the DB-level idempotency guarantee.
            async with self._sf() as session:
                original = await self._find(session, idempotency_key)
            if original is None:  # pragma: no cover - not a duplicate-key violation
                raise
            return _result(original, replayed=True)

    @staticmethod
    async def _find(session: AsyncSession, idempotency_key: str) -> LedgerEntry | None:
        entry: LedgerEntry | None = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
        )
        return entry

    @staticmethod
    async def _house_wallet(session: AsyncSession, currency: str, mode: str) -> Wallet:
        house = await session.scalar(
            select(Wallet)
            .where(
                Wallet.user_id == SYSTEM_USER_ID,
                Wallet.currency == currency,
                Wallet.mode == mode,
            )
            .with_for_update()
        )
        if house is None:
            raise WalletNotFound(f"no house wallet for currency {currency} mode {mode}")
        return house


def _require_positive(amount_minor: int) -> None:
    if amount_minor <= 0:
        raise ValueError(f"amount_minor must be a positive integer, got {amount_minor}")
