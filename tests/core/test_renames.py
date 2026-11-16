from typing import Any

from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.snapshots.test_relations import BENYLIN, MEMBER, WHEEL, link
from tests.snapshots.test_snapshots import DESKTOP, history, item, put
from tests.tokens import bearer
from thoth.core.auth import READ, report
from thoth.models import Relation


async def rename(client: AsyncClient, headers: dict[str, str] | None = None, /, **body: Any) -> Any:
    request = {"type": "host", "key": "clockmost-desktop", "new_key": "battlestation", "reason": "new name", **body}
    return await client.post("/v1/renames", json=request, headers=headers)


async def test_rename_keeps_the_item_and_its_history(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    before = await item(db, "host", "clockmost-desktop")
    response = await rename(client, new_name="battlestation")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["key"], body["name"]) == (str(before.id), "battlestation", "battlestation")
    assert body["change_reason"] == "rename clockmost-desktop -> battlestation: new name"
    [old] = await history(db, await item(db, "host", "battlestation"))
    assert old.key == "clockmost-desktop"


async def test_links_follow_the_renamed_item(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    await link(client, "host-scan", "clockmost-desktop", MEMBER)
    await rename(client, bearer(report("host-scan", "clockmost-desktop")), type="group", key=WHEEL["key"],
           new_key="linux/clockmost-desktop/admins")  # its own source may rename it  # fmt: skip
    async with AsyncSession(db) as session:
        [row] = (await session.exec(select(Relation))).all()
    assert row.to_item == (await item(db, "group", "linux/clockmost-desktop/admins")).id


async def test_refusals(client: AsyncClient) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    await put(client, "host-scan", "clockmost", {**DESKTOP, "key": "clockmost", "name": "clockmost"})
    cases = [
        ({"key": "nope"}, 404, "no host nope"),
        ({"new_key": "Bad Key"}, 422, "doesn't match"),
        ({"new_key": "clockmost"}, 409, "already exists"),
        ({"type": "toaster"}, 422, "unknown type"),
    ]
    for body, status, detail in cases:
        response = await rename(client, **body)
        assert (response.status_code, response.headers["content-type"]) == (status, "application/problem+json")
        assert detail in response.json()["detail"], body
    for headers in (bearer(report("authentik-sync", "all")), bearer(READ)):  # another source; read-only
        response = await rename(client, headers)
        assert response.status_code == 403
        assert "needs thoth.manual, or to report as host-scan" in response.json()["detail"]
