"""Item snapshots through the API, against the real database: D51 (strict slices), D52 (scope), D53/D54 (misses)."""

import itertools
from typing import Any

from httpx2 import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.conftest import run_sql
from thoth.db.session import write_as
from thoth.models import Item, ItemHistory, Status

DESKTOP = {"type": "host", "key": "clockmost-desktop", "name": "clockmost-desktop",
           "attributes": {"kind": "physical", "os": "CachyOS"}}  # fmt: skip


def account(login: str, email: str | None = None, host: str = "clockmost-desktop") -> dict[str, Any]:
    return {"type": "account", "key": f"linux/{host}/{login}", "name": login, "attributes": {"email": email}}


_scans = itertools.count()


async def put(
    client: AsyncClient, source: str, scope: str, *items: dict[str, Any], scan: str | None = None
) -> Response:
    """Phase 1 of a scan; a new scan id each call unless one is given."""
    return await client.put(
        f"/v1/scans/{scan or f'scan-{next(_scans)}'}/{source}/{scope}/items", json={"items": list(items)}
    )


async def item(db: AsyncEngine, type_name: str, key: str) -> Item:
    async with AsyncSession(db) as session:
        return (await session.exec(select(Item).where(Item.type == type_name, Item.key == key))).one()


async def history(db: AsyncEngine, it: Item) -> list[ItemHistory]:
    async with AsyncSession(db) as session:
        return list(await session.exec(select(ItemHistory).where(ItemHistory.id == it.id)))


async def test_first_snapshot_creates_items(client: AsyncClient, db: AsyncEngine) -> None:
    response = await put(client, "host-scan", "clockmost-desktop", DESKTOP, account("benylin", "lealmariop@gmail.com"))
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 2, "changed": 0, "unchanged": 0, "missed": 0, "for_anubis": [], "skipped": []}
    host = await item(db, "host", "clockmost-desktop")
    assert (host.status, host.source, host.attributes) == (Status.ACTIVE, "host-scan", DESKTOP["attributes"])
    assert host.change_reason == "snapshot from host-scan@clockmost-desktop"


async def test_same_snapshot_again_changes_nothing(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    first = await item(db, "host", "clockmost-desktop")
    assert (await put(client, "host-scan", "clockmost-desktop", DESKTOP)).json()["unchanged"] == 1
    again = await item(db, "host", "clockmost-desktop")
    assert again.last_seen > first.last_seen
    assert await history(db, again) == []  # last_seen moving is not a version


async def test_a_changed_value_is_a_new_version(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    upgraded = {**DESKTOP, "attributes": {"kind": "physical", "os": "CachyOS 2026.11"}}
    assert (await put(client, "host-scan", "clockmost-desktop", upgraded)).json()["changed"] == 1
    [old] = await history(db, await item(db, "host", "clockmost-desktop"))
    assert old.attributes["os"] == "CachyOS"


async def test_a_missing_field_refuses_the_whole_snapshot(client: AsyncClient, db: AsyncEngine) -> None:
    broken = {**DESKTOP, "attributes": {"kind": "physical"}}  # os missing: the scanner broke (D51)
    response = await put(client, "host-scan", "clockmost-desktop", account("benylin"), broken)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"] == [{"msg": "items[1] (host clockmost-desktop): os: Field required"}]
    async with AsyncSession(db) as session:  # nothing applied, not even the valid account
        assert (await session.exec(select(Item))).all() == []
    [run] = (await client.get("/v1/snapshots")).json()
    assert (run["accepted"], run["summary"]["errors"]) == (
        False,
        ["items[1] (host clockmost-desktop): os: Field required"],
    )


async def test_null_is_a_value(client: AsyncClient) -> None:
    assert (
        await put(client, "host-scan", "clockmost-desktop", {**DESKTOP, "attributes": {"kind": "vm", "os": None}})
    ).is_success


async def test_a_field_the_source_does_not_own_is_refused(client: AsyncClient) -> None:
    sneaky = {"type": "person", "key": "mario", "name": "Mario", "attributes": {"names": [], "emails": []}}
    response = await put(client, "authentik-sync", "all", sneaky)  # person fields belong to manual
    assert response.status_code == 422
    assert "names: Extra inputs are not permitted" in response.json()["errors"][0]["msg"]


async def test_items_outside_the_scope_are_refused(client: AsyncClient) -> None:
    response = await put(client, "host-scan", "clockmost-desktop", account("claude-admin", host="clockmost"))
    assert response.status_code == 422
    assert response.json()["errors"][0]["msg"].endswith("outside scope clockmost-desktop")


async def test_whole_system_sources_use_scope_all(client: AsyncClient) -> None:
    response = await put(client, "authentik-sync", "clockmost")
    assert response.status_code == 422
    assert "its scope is 'all'" in response.json()["errors"][0]["msg"]


async def test_a_whole_system_source_only_reports_its_types(client: AsyncClient) -> None:
    host = {"type": "host", "key": "made-up", "name": "made-up", "attributes": {}}
    app = {"type": "app", "key": "echo", "name": "echo", "attributes": {"template": "nuwa/python"}}
    response = await put(client, "shikigami", "all", app, host)
    assert response.status_code == 422  # all or nothing: the app isn't written either
    assert [e["msg"] for e in response.json()["errors"]] == ["items[1] (host made-up): outside scope all"]
    assert (await put(client, "shikigami", "all", app)).status_code == 200


async def test_bad_keys_and_unknown_types_are_refused(client: AsyncClient) -> None:
    response = await put(client, "host-scan", "clockmost-desktop", {**DESKTOP, "key": "Clockmost-Desktop"},
                   {"type": "toaster", "key": "x", "name": "x", "attributes": {}})  # fmt: skip
    messages = [e["msg"] for e in response.json()["errors"]]
    assert any("key doesn't match" in m for m in messages)
    assert any("unknown type" in m for m in messages)


async def test_three_misses_go_to_anubis_and_coming_back_resets(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP, account("olduser"))
    for expected in ([], [], ["account linux/clockmost-desktop/olduser"]):
        response = (await put(client, "host-scan", "clockmost-desktop", DESKTOP)).json()
        assert (response["missed"], response["for_anubis"]) == (1, expected)
    assert (await item(db, "account", "linux/clockmost-desktop/olduser")).status == Status.ACTIVE  # only Anubis decides
    await put(client, "host-scan", "clockmost-desktop", DESKTOP, account("olduser"))
    assert (await put(client, "host-scan", "clockmost-desktop", DESKTOP)).json()["for_anubis"] == []  # count restarted


async def test_scopes_do_not_miss_each_other(client: AsyncClient) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    clockmost = {**DESKTOP, "key": "clockmost", "name": "clockmost"}
    assert (await put(client, "host-scan", "clockmost", clockmost)).json()["missed"] == 0
    assert (await put(client, "host-scan", "clockmost-desktop", DESKTOP)).json()["missed"] == 0


async def test_declared_is_planned_until_seen_and_declaration_wins(client: AsyncClient, db: AsyncEngine) -> None:
    route = {"type": "route", "key": "echo.clockmost.com", "name": "echo"}
    await put(client, "shikigami", "all", {**route, "attributes": {"auth": "authentik"}})
    assert (await item(db, "route", "echo.clockmost.com")).status == Status.PLANNED
    await put(client, "caddy-scan", "all", {**route, "attributes": {"auth": "none", "tls": True}})
    seen = await item(db, "route", "echo.clockmost.com")
    assert seen.status == Status.ACTIVE
    assert seen.attributes == {"auth": "authentik", "tls": True}  # Shikigami ranks first for auth (D49)
    assert seen.source == "shikigami"  # the first reporter owns the name


async def test_seeing_it_never_undoes_a_verdict(client: AsyncClient, db: AsyncEngine) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    async with AsyncSession(db) as session:
        await write_as(session, "shikigami", "retire request #1 finished", retire=True)
        await run_sql(session, "update item set status = 'decommissioning'")
        await session.commit()
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    assert (
        await item(db, "host", "clockmost-desktop")
    ).status == Status.DECOMMISSIONING  # Anubis decides, not scanners


async def test_latest_snapshot_per_source(client: AsyncClient) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    await put(client, "host-scan", "clockmost-desktop", DESKTOP)
    await put(client, "authentik-sync", "all")
    runs = (await client.get("/v1/snapshots")).json()
    assert all(r["kind"] == "items" for r in runs)
    assert [(r["source"], r["scope"], r["accepted"]) for r in runs] == [
        ("authentik-sync", "all", True),
        ("host-scan", "clockmost-desktop", True),
    ]


async def test_resending_a_scan_counts_no_extra_misses(client: AsyncClient) -> None:
    await put(client, "host-scan", "clockmost-desktop", DESKTOP, account("olduser"))
    for _ in range(3):  # the same scan, retried three times
        response = (await put(client, "host-scan", "clockmost-desktop", DESKTOP, scan="scan-retried")).json()
    assert response["for_anubis"] == []  # one scan without it, not three


async def test_things_that_come_and_go_wait_for_time_not_scans(client: AsyncClient) -> None:
    phone = {"type": "device", "key": "a4:5e:60:12:34:56", "name": "phone", "attributes": {}}
    await put(client, "r7000-scan", "all", phone)
    for _ in range(5):  # off the Wi-Fi for five scans: normal for a phone
        assert (await put(client, "r7000-scan", "all")).json()["for_anubis"] == []


async def test_the_cluster_scan_confirms_declared_apps_and_never_creates_them(
    client: AsyncClient, db: AsyncEngine
) -> None:
    await put(client, "shikigami", "all", {"type": "app", "key": "tanuki", "name": "tanuki",
                                     "attributes": {"template": "nuwa/python"}})  # fmt: skip
    workload = {"type": "workload", "key": "clockmost/tanuki/deployment/tanuki", "name": "tanuki", "attributes": {}}
    stray = {"type": "app", "key": "stray", "name": "stray", "attributes": {}}
    response = await put(client, "k8s-scan", "clockmost", workload, stray)
    assert response.status_code == 422  # all or nothing: the workload isn't written either
    assert [e["msg"] for e in response.json()["errors"]] == [
        "items[1] (app stray): not declared - k8s-scan can confirm one, never create it"
    ]
    assert (await item(db, "app", "tanuki")).status == Status.PLANNED

    seen_app = {"type": "app", "key": "tanuki", "name": "tanuki", "attributes": {}}
    assert (await put(client, "k8s-scan", "clockmost", workload, seen_app)).status_code == 200
    tanuki = await item(db, "app", "tanuki")
    assert (tanuki.status, tanuki.source, tanuki.attributes) == (
        Status.ACTIVE,
        "shikigami",
        {"template": "nuwa/python"},
    )


async def test_the_cluster_scan_sends_no_app_fields(client: AsyncClient) -> None:
    await put(client, "shikigami", "all", {"type": "app", "key": "tanuki", "name": "tanuki",
                                     "attributes": {"template": "nuwa/python"}})  # fmt: skip
    claimed = {"type": "app", "key": "tanuki", "name": "tanuki", "attributes": {"template": "nuwa/rust"}}
    assert (await put(client, "k8s-scan", "clockmost", claimed)).status_code == 422  # the template is Shikigami's
