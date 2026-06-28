"""FastAPI application entrypoint: health probe + the generic game routers."""

from fastapi import FastAPI

from app.api import router as games_router
from app.api.crash import router as crash_state_router
from app.api.fairness import router as fairness_router
from app.auth import router as auth_router
from app.ws.crash import router as crash_ws_router
from app.ws.poker import router as poker_ws_router

app = FastAPI(title="casino-lacta", version="0.0.0")
app.include_router(auth_router)
# The Crash-specific resume endpoint MUST be registered BEFORE the generic games
# router so ``GET /games/originals.crash/state`` resolves to the Crash handler (the
# shared-round projection), not the generic ``/games/{game_id}/state``.
app.include_router(crash_state_router)
app.include_router(games_router)
app.include_router(fairness_router)
# WS read-only fan-out for Crash. Including the router only exposes the endpoint;
# it does NOT start the authoritative round actor (a separate singleton process
# runs CrashActor.run_forever), so importing the app spins no crash loop.
app.include_router(crash_ws_router)
# WS per-seat fan-out for PvP poker. Like Crash, including the router only exposes the
# endpoint; the authoritative table actor (PokerActor) is driven by a separate partition
# process, so importing the app spins no poker loop. Each seat subscribes ONLY to its own
# redacted channel — the server never publishes a seat's hole cards anywhere else.
app.include_router(poker_ws_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
