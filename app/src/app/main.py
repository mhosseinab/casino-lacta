"""FastAPI application entrypoint: health probe + the generic game routers."""

from fastapi import FastAPI

from app.api import router as games_router
from app.api.fairness import router as fairness_router
from app.auth import router as auth_router
from app.ws.crash import router as crash_ws_router

app = FastAPI(title="casino-lacta", version="0.0.0")
app.include_router(auth_router)
app.include_router(games_router)
app.include_router(fairness_router)
# WS read-only fan-out for Crash. Including the router only exposes the endpoint;
# it does NOT start the authoritative round actor (a separate singleton process
# runs CrashActor.run_forever), so importing the app spins no crash loop.
app.include_router(crash_ws_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
