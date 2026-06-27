"""Wallet / double-entry ledger (app member).

The ledger is the ONLY path that moves credits: every op writes a balanced
two-row :class:`~app.db.models.LedgerEntry` set sharing one ``txn_id`` whose
counterparty is the reserved SYSTEM/house wallet, and reconciles the
``wallet.balance_minor`` projection in the SAME transaction. Balances are never
mutated outside this path.
"""

from app.wallet.ledger import (
    InsufficientFunds,
    Ledger,
    LedgerError,
    LedgerResult,
    ReconcileError,
    UnknownOp,
    WalletNotFound,
)

__all__ = [
    "InsufficientFunds",
    "Ledger",
    "LedgerError",
    "LedgerResult",
    "ReconcileError",
    "UnknownOp",
    "WalletNotFound",
]
