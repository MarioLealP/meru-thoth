"""Anubis's side of Thoth (D53, D118): the unseen queue and verdicts - `retired` only with thoth.verdict, enforced
twice."""

from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.conftest import run_sql
from tests.snapshots.test_relations import BENYLIN, MEMBER, WHEEL, link, relations
from tests.snapshots.test_snapshots import DESKTOP, account, history, item, put
from tests.tokens import bearer
from thoth.core.auth import READ, RETIRE, VERDICT
from thoth.db.session import write_as
from thoth.models import Status

ANUBIS = bearer(READ, VERDICT, name="anubis")
OLDUSER = account("olduser")
OLDUSER_KEY = "linux/clockmost-desktop/olduser"


async def miss(client: AsyncClient, times: int) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP, OLDUSER)
    for _ in range(times):
        await put(client, "host-scan", "clockmost-desktop", DESKTOP)


async def verdict(client: AsyncClient, status: str, key: str = OLDUSER_KEY, headers: dict[str, str] = ANUBIS) -> Any:
    body = {"type": "account", "key": key, "status": status, "reason": "gone: getent passwd olduser found nothing"}
    return await client.post("/v1/verdicts", json=body, headers=headers)


async def test_three_misses_put_an_item_on_the_unseen_queue(client: AsyncClient) -> None:
    await miss(client, 3)
    response = await client.get("/v1/unseen", headers=ANUBIS)
    assert response.status_code == 200, response.text
    [entry] = response.json()
    assert (entry["item"]["key"], entry["source"], entry["scope"], entry["missed_scans"]) == (
        OLDUSER_KEY,
        "host-scan",
        "clockmost-desktop",
        3,
    )


async def test_two_misses_are_not_queued(client: AsyncClient) -> None:
    await miss(client, 2)
    assert (await client.get("/v1/unseen", headers=ANUBIS)).json() == []


async def test_things_that_come_and_go_are_not_queued_by_scans(client: AsyncClient) -> None:
    await put(
        client, "r7000-scan", "all", {"type": "device", "key": "a4:5e:60:12:34:56", "name": "phone", "attributes": {}}
    )
    for _ in range(5):
        await put(client, "r7000-scan", "all")
    assert (await client.get("/v1/unseen", headers=ANUBIS)).json() == []


async def test_a_retiring_item_is_not_queued(client: AsyncClient, db: AsyncEngine) -> None:
    await miss(client, 3)
    async with AsyncSession(db) as session:
        await write_as(session, "shikigami", "retire request #1", retire=True)
        await run_sql(session, f"update item set status = 'decommissioning' where key = '{OLDUSER_KEY}'")
        await session.commit()
    assert (await client.get("/v1/unseen", headers=ANUBIS)).json() == []  # its absence is expected


async def test_the_queue_needs_read(anon: AsyncClient) -> None:
    assert (await anon.get("/v1/unseen")).status_code == 401


async def test_retired_leaves_the_queue_and_keeps_the_reason(client: AsyncClient, db: AsyncEngine) -> None:
    await miss(client, 3)
    response = await verdict(client, "retired")
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["changed_by"]) == ("retired", "anubis")
    assert response.json()["change_reason"] == "retired: gone: getent passwd olduser found nothing"
    assert (await client.get("/v1/unseen", headers=ANUBIS)).json() == []
    assert [v.status for v in await history(db, await item(db, "account", OLDUSER_KEY))] == [Status.ACTIVE]


async def test_missing_then_retired(client: AsyncClient) -> None:
    await miss(client, 3)
    assert (await verdict(client, "missing")).json()["status"] == "missing"
    assert len((await client.get("/v1/unseen", headers=ANUBIS)).json()) == 1  # still unseen: checked again next pass
    assert (await verdict(client, "retired")).json()["status"] == "retired"


async def test_a_verdict_already_given_is_answered_as_it_is(client: AsyncClient, db: AsyncEngine) -> None:
    await miss(client, 3)
    await verdict(client, "retired")
    again = await verdict(client, "retired")
    assert (again.status_code, again.json()["status"]) == (200, "retired")
    assert len(await history(db, await item(db, "account", OLDUSER_KEY))) == 1  # no new version


async def test_retiring_takes_the_links_with_it(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    await link(client, "host-scan", "clockmost-desktop", MEMBER)
    response = await verdict(client, "retired", key=BENYLIN["key"])
    assert response.status_code == 200, response.text
    assert [r.status for r in await relations(db)] == [Status.RETIRED]


@pytest.mark.parametrize(
    ("start", "wanted"),
    [("planned", "retired"), ("decommissioning", "retired"), ("missing", "missing"), ("retired", "missing")],
)
async def test_verdicts_only_from_their_states(client: AsyncClient, db: AsyncEngine, start: str, wanted: str) -> None:
    await miss(client, 3)
    flags = {"retire": True} if start in ("decommissioning",) else {"verdict": start == "retired"}
    async with AsyncSession(db) as session:
        await write_as(session, "test", "set up", **flags)
        await run_sql(session, f"update item set status = '{start}' where key = '{OLDUSER_KEY}'")
        await session.commit()
    response = await verdict(client, wanted)
    if start == wanted:
        assert response.status_code == 200
    else:
        assert response.status_code == 409, response.text
        assert f"is {start}" in response.json()["detail"]


async def test_refusals(client: AsyncClient) -> None:
    await miss(client, 3)
    assert (await verdict(client, "retired", headers=bearer(READ, RETIRE, name="shikigami"))).status_code == 403
    assert (await verdict(client, "retired", key="linux/clockmost-desktop/nobody")).status_code == 404
    assert (await verdict(client, "active")).status_code == 422  # Anubis never brings things back; scanners do


async def test_the_database_refuses_retired_without_the_verdict_flag(db: AsyncEngine, client: AsyncClient) -> None:
    await miss(client, 3)
    async with AsyncSession(db) as session:
        await write_as(session, "someone", "trying")
        with pytest.raises(DBAPIError, match="only anubis retires"):
            await run_sql(session, f"update item set status = 'retired' where key = '{OLDUSER_KEY}'")
