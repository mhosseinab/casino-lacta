"""FastAPI application entrypoint. S1 scaffold: a single health probe."""

from fastapi import FastAPI

app = FastAPI(title="casino-lacta", version="0.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
