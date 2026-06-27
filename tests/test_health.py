"""S1 scaffold smoke test: GET /health returns 200 + {"status": "ok"}."""

import httpx

from app.main import app


async def test_health_ok() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
