"""HTTP transport (app member) — generic game routers over the shared bet loop.

Transport is a thin gateway: it validates the request envelope, calls the
shared bet loop / state services, and maps domain errors to HTTP status. It
decides NOTHING about outcomes or balances (server-authoritative lives in the
loop + ledger).
"""

from app.api.games import router

__all__ = ["router"]
