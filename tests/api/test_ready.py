from datetime import UTC, datetime

from httpx2 import AsyncClient
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.catalog import ITEMS, RELATIONS
from thoth.db.session import write_as
from thoth.models import Item, ItemObservation, ItemType, RelationType


async def add_host(db: AsyncEngine, key: str, attributes: dict, observed: dict | None = None) -> None:
    now = datetime.now(UTC)
    async with AsyncSession(db) as session:
        await write_as(session, "test", "setup")
        item = Item(type="host", key=key, name=key, source="host-scan", attributes=attributes, first_seen=now,
                    last_seen=now)  # fmt: skip
        session.add(item)
        await session.flush()
        if observed is not None:
            session.add(ItemObservation(item_id=item.id, source="host-scan", scope=key.lower(), attributes=observed,
                                        first_seen=now, last_seen=now, last_scan="test"))  # fmt: skip
        await session.commit()


async def test_ready_syncs_the_catalog(client: AsyncClient, db: AsyncEngine) -> None:
    async with AsyncSession(db) as session:
        await session.exec(delete(ItemType))
        await session.commit()
    response = await client.get("/ready")
    assert response.status_code == 200, response.text
    async with AsyncSession(db) as session:
        assert (await session.exec(select(func.count()).select_from(ItemType))).one() == len(ITEMS)
        assert (await session.exec(select(func.count()).select_from(RelationType))).one() == len(RELATIONS)


async def test_rows_that_fit_are_ready(client: AsyncClient, db: AsyncEngine) -> None:
    await add_host(db, "clockmost", {"kind": "physical"}, {"kind": "physical"})
    assert (await client.get("/ready")).status_code == 200


async def test_misfit_rows_keep_it_unready(client: AsyncClient, db: AsyncEngine) -> None:
    await add_host(db, "Bad_Key", {})
    await add_host(db, "clockmost", {"kind": "mainframe"}, {"kind": "mainframe"})
    response = await client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "host Bad_Key: key doesn't match" in detail
    assert "host clockmost, from host-scan@clockmost: kind:" in detail
    assert (await client.get("/heartbeat")).status_code == 200  # alive, just not ready


async def test_catalog_endpoint(client: AsyncClient) -> None:
    body = (await client.get("/v1/catalog")).json()
    assert body["items"]["route"]["schema"]["properties"]["auth"]["x-owners"] == ["shikigami", "caddy-scan"]
