"""Reading the past (D39): an item's versions, and the inventory as of a moment."""

import itertools
from datetime import UTC, datetime
from typing import Any

from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.snapshots.test_snapshots import DESKTOP, item, put
from tests.tokens import bearer
from thoth.core.auth import MANUAL


async def as_of(client: AsyncClient, at: datetime, **filters: Any) -> list[tuple[str, str]]:
    response = await client.get("/v1/items", params={"as_of": at.isoformat(), **filters})
    assert response.status_code == 200, response.text
    return [(i["key"], i["attributes"]["os"]) for i in response.json()]


async def three_versions(client: AsyncClient) -> list[datetime]:
    """Created, upgraded, renamed - with a moment captured after each."""
    moments = [datetime.now(UTC)]
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    moments.append(datetime.now(UTC))
    await put(
        client, "host-scan", "clockmost-desktop", {**DESKTOP, "attributes": {"kind": "physical", "os": "CachyOS 2"}}
    )
    moments.append(datetime.now(UTC))
    await client.post("/v1/renames", json={"type": "host", "key": "clockmost-desktop", "new_key": "battlestation",
                                     "reason": "new name"}, headers=bearer(MANUAL, name="mario"))  # fmt: skip
    moments.append(datetime.now(UTC))
    return moments


async def test_versions_newest_first_with_who_and_why(client: AsyncClient, db: AsyncEngine) -> None:
    await three_versions(client)
    response = await client.get(f"/v1/items/{(await item(db, 'host', 'battlestation')).id}/history")
    versions = response.json()
    assert [(v["key"], v["attributes"]["os"], v["changed_by"]) for v in versions] == [
        ("battlestation", "CachyOS 2", "mario"),
        ("clockmost-desktop", "CachyOS 2", "svc+test"),
        ("clockmost-desktop", "CachyOS", "svc+test"),
    ]
    assert versions[0]["valid_to"] is None
    for newer, older in itertools.pairwise(versions):
        assert older["valid_to"] == newer["valid_from"]  # no gaps, no overlaps


async def test_as_of_each_moment(client: AsyncClient) -> None:
    before, created, upgraded, renamed = await three_versions(client)
    assert await as_of(client, before) == []  # didn't exist yet
    assert await as_of(client, created) == [("clockmost-desktop", "CachyOS")]
    assert await as_of(client, upgraded) == [("clockmost-desktop", "CachyOS 2")]
    assert await as_of(client, renamed) == [("battlestation", "CachyOS 2")]


async def test_as_of_filters_on_the_old_values(client: AsyncClient) -> None:
    _, created, _, _ = await three_versions(client)
    assert await as_of(client, created, key="clockmost-desktop") == [("clockmost-desktop", "CachyOS")]
    assert await as_of(client, created, key="battlestation") == []


async def test_history_of_an_unknown_item(client: AsyncClient) -> None:
    response = await client.get("/v1/items/00000000-0000-0000-0000-000000000000/history")
    assert (response.status_code, response.headers["content-type"]) == (404, "application/problem+json")
