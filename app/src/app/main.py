"""FastAPI application entrypoint: health probe + the generic game routers."""

from fastapi import FastAPI

from app.api import router as games_router
from app.api.fairness import router as fairness_router
from app.auth import router as auth_router

app = FastAPI(title="casino-lacta", version="0.0.0")
app.include_router(auth_router)
app.include_router(games_router)
app.include_router(fairness_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
