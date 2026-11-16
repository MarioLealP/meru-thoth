"""Shikigami's retire pass in Thoth (D85): decommissioning, then retained - and planned again on a restore (D106) - only
with thoth.retire, enforced twice."""

from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.conftest import run_sql
from tests.snapshots.test_snapshots import history, item, put
from tests.tokens import bearer
from thoth.core.auth import READ, RETIRE, report
from thoth.db.session import write_as
from thoth.models import Status

SHIKIGAMI = bearer(RETIRE, report("shikigami", "all"), name="shikigami")
KITSUNE = {"type": "app", "key": "kitsune", "name": "kitsune", "attributes": {"template": "nuwa/python"}}


async def step(client: AsyncClient, status: str, app: str = "kitsune", headers: dict[str, str] = SHIKIGAMI) -> Any:
    return await client.post(
        "/v1/retire-steps", json={"app": app, "status": status, "reason": "requests#13"}, headers=headers
    )


async def test_an_app_goes_decommissioning_then_retained(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    first = await step(client, "decommissioning")
    assert first.status_code == 200, first.text
    assert (first.json()["status"], first.json()["changed_by"]) == ("decommissioning", "shikigami")
    assert first.json()["change_reason"] == "decommissioning: requests#13"
    assert (await step(client, "retained")).json()["status"] == "retained"
    kitsune = await item(db, "app", "kitsune")
    assert [v.status for v in await history(db, kitsune)] == [Status.PLANNED, Status.DECOMMISSIONING]


async def test_a_retried_step_is_answered_as_it_is(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    await step(client, "decommissioning")
    again = await step(client, "decommissioning")
    assert (again.status_code, again.json()["status"]) == (200, "decommissioning")
    assert len(await history(db, await item(db, "app", "kitsune"))) == 1  # no new version


async def test_steps_go_in_order_and_never_back(client: AsyncClient) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    early = await step(client, "retained")
    assert early.status_code == 409
    assert "is planned: retained comes after" in early.json()["detail"]
    await step(client, "decommissioning")
    await step(client, "retained")
    assert (await step(client, "decommissioning")).status_code == 409


async def test_seeing_a_retiring_app_does_not_bring_it_back(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    await step(client, "decommissioning")
    seen = {"type": "app", "key": "kitsune", "name": "kitsune", "attributes": {}}
    assert (await put(client, "k8s-scan", "clockmost", seen)).status_code == 200  # a pod still stopping
    assert (await item(db, "app", "kitsune")).status == Status.DECOMMISSIONING


@pytest.mark.parametrize("reached", ["decommissioning", "retained"])
async def test_a_restore_takes_it_back_to_planned_and_a_scan_makes_it_active(
    client: AsyncClient, db: AsyncEngine, reached: str
) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    await step(client, "decommissioning")
    await step(client, reached)
    back = await step(client, "planned")
    assert back.status_code == 200, back.text
    assert (back.json()["status"], back.json()["change_reason"]) == ("planned", "planned: requests#13")
    seen = {"type": "app", "key": "kitsune", "name": "kitsune", "attributes": {}}
    await put(client, "k8s-scan", "clockmost", seen)
    assert (await item(db, "app", "kitsune")).status == Status.ACTIVE
    again = await step(client, "planned")  # a retried restore after the scan: answered as it is
    assert (again.status_code, again.json()["status"]) == (200, "active")


async def test_a_restored_app_can_be_retired_again(client: AsyncClient) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    for status in ("decommissioning", "retained", "planned", "decommissioning", "retained"):
        assert (await step(client, status)).status_code == 200


async def test_refusals(client: AsyncClient) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    assert (await step(client, "decommissioning", app="nope")).status_code == 404
    assert (await step(client, "retired")).status_code == 422  # only Anubis retires, and not here
    for headers in (bearer(READ), bearer(report("shikigami", "all"))):
        response = await step(client, "decommissioning", headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "needs thoth.retire"


@pytest.mark.parametrize("status", ["decommissioning", "retained"])
async def test_the_database_refuses_it_without_the_retire_flag(
    client: AsyncClient, db: AsyncEngine, status: str
) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    async with AsyncSession(db) as session:
        await write_as(session, "manual", "trying it by hand")
        with pytest.raises(DBAPIError, match="only the retire pass sets"):
            await run_sql(session, f"update item set status = '{status}'")


@pytest.mark.parametrize("to", ["planned", "active"])
async def test_the_database_refuses_bringing_it_back_without_the_retire_flag(
    client: AsyncClient, db: AsyncEngine, to: str
) -> None:
    await put(client, "shikigami", "all", KITSUNE)
    await step(client, "decommissioning")
    await step(client, "retained")
    async with AsyncSession(db) as session:
        await write_as(session, "manual", "trying it by hand")
        with pytest.raises(DBAPIError, match="only the retire pass takes a item row out of retained"):
            await run_sql(session, f"update item set status = '{to}'")
