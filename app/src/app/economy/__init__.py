"""Economy (app member) — the SINGLE OWNER of house → player grants.

S8 ships only the welcome grant here; S36 (faucets) owns/extends this module
(daily streak, hourly timer, level-up) WITHOUT auth changing — the
``welcome_grant`` signature is the stable contract auth depends on (DIP). All
credits enter ONLY via :meth:`Ledger.grant` (a balanced two-row entry with the
SYSTEM/house counterparty); economy policy (amount + idempotency key) lives here,
never scattered across callers.
"""

from app.economy.faucets import (
    WELCOME_GRANT_MINOR,
    welcome_grant,
    welcome_grant_key,
)

__all__ = [
    "WELCOME_GRANT_MINOR",
    "welcome_grant",
    "welcome_grant_key",
]
