from collections.abc import AsyncIterator

import pytest
from httpx2 import ASGITransport, AsyncClient

from thoth.api import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_heartbeat(client: AsyncClient) -> None:
    response = await client.get("/heartbeat")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_errors_are_problem_details(client: AsyncClient) -> None:
    response = await client.get("/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found"}
