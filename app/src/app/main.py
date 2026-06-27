"""FastAPI application entrypoint: health probe + the generic game routers."""

from fastapi import FastAPI

from app.api import router as games_router

app = FastAPI(title="casino-lacta", version="0.0.0")
app.include_router(games_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
