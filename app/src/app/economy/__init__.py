"""Economy (app member) — the SINGLE OWNER of house → player grants.

S8 ships only the welcome grant here; S36 (faucets) owns/extends this module
(daily streak, hourly timer, level-up) WITHOUT auth changing — the
``welcome_grant`` signature is the stable contract auth depends on (DIP). All
credits enter ONLY via :meth:`Ledger.grant` (a balanced two-row entry with the
SYSTEM/house counterparty); economy policy (amount + idempotency key) lives here,
never scattered across callers.
"""

from app.economy.faucets import (
    DAILY_BASE_MINOR,
    DAILY_STREAK_CAP,
    DAILY_STREAK_STEP_MINOR,
    HOURLY_GRANT_MINOR,
    LEVEL_UP_BASE_MINOR,
    WELCOME_GRANT_MINOR,
    FaucetResult,
    FaucetStore,
    RedisFaucetStore,
    daily_streak_claim,
    daily_streak_key,
    hourly_claim,
    hourly_key,
    level_up_claim,
    level_up_key,
    welcome_grant,
    welcome_grant_key,
)

__all__ = [
    "DAILY_BASE_MINOR",
    "DAILY_STREAK_CAP",
    "DAILY_STREAK_STEP_MINOR",
    "HOURLY_GRANT_MINOR",
    "LEVEL_UP_BASE_MINOR",
    "WELCOME_GRANT_MINOR",
    "FaucetResult",
    "FaucetStore",
    "RedisFaucetStore",
    "daily_streak_claim",
    "daily_streak_key",
    "hourly_claim",
    "hourly_key",
    "level_up_claim",
    "level_up_key",
    "welcome_grant",
    "welcome_grant_key",
]
