"""Thoth builds the web from one-hop links (D55): chains, diamonds, loops, depth, retired links."""

from typing import Any

from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.conftest import run_sql
from tests.snapshots.test_relations import link, rel
from tests.snapshots.test_snapshots import item, put
from thoth.db.session import write_as


def app(name: str) -> dict[str, Any]:
    return {"type": "app", "key": name, "name": name, "attributes": {"template": None}}


async def web(client: AsyncClient, *links: tuple[str, str]) -> None:
    """Declare apps and `a uses b` links the way Shikigami would."""
    names = sorted({n for pair in links for n in pair})
    await put(client, "shikigami", "all", *(app(n) for n in names))
    assert (await link(client, "shikigami", "all", *(rel("uses", app(a), app(b)) for a, b in links))).is_success


async def walk(client: AsyncClient, db: AsyncEngine, name: str, direction: str, depth: int = 10) -> dict[str, Any]:
    """One side of the web, from GET /web?direction=."""
    web_ = await get_web(client, db, name, depth=depth, direction=direction)
    return {"items": web_[direction], "cycles": web_["cycles"]}


async def get_web(client: AsyncClient, db: AsyncEngine, name: str, **params: Any) -> dict[str, Any]:
    response = await client.get(f"/v1/items/{(await item(db, 'app', name)).id}/web", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def reached(result: dict[str, Any]) -> list[tuple[str, int]]:
    return [(s["item"]["key"], s["depth"]) for s in result["items"]]


async def test_a_chain_both_ways(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("echo", "shikigami"), ("shikigami", "temporal"), ("temporal", "temporal-db"))
    assert reached(await walk(client, db, "temporal-db", "impact")) == [("temporal", 1), ("shikigami", 2), ("echo", 3)]
    assert reached(await walk(client, db, "echo", "dependencies")) == [
        ("shikigami", 1),
        ("temporal", 2),
        ("temporal-db", 3),
    ]


async def test_depth_one_is_direct_neighbours(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("echo", "shikigami"), ("shikigami", "temporal"))
    assert reached(await walk(client, db, "echo", "dependencies", depth=1)) == [("shikigami", 1)]


async def test_the_link_it_came_by(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("echo", "shikigami"))
    [step] = (await walk(client, db, "shikigami", "impact"))["items"]
    assert (step["via"]["from"]["key"], step["via"]["type"], step["via"]["to"]["key"]) == ("echo", "uses", "shikigami")


async def test_a_diamond_lists_each_item_once(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"))
    assert reached(await walk(client, db, "a", "dependencies")) == [("b", 1), ("c", 1), ("d", 2)]


async def test_a_loop_is_reported_not_followed(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("a", "b"), ("b", "c"), ("c", "a"))
    result = await walk(client, db, "a", "dependencies", depth=50)
    assert reached(result) == [("b", 1), ("c", 2)]  # a itself is the start, not repeated
    assert [(c["from"]["key"], c["to"]["key"]) for c in result["cycles"]] == [("c", "a")]


async def test_two_apps_using_each_other(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("a", "b"), ("b", "a"))
    result = await walk(client, db, "a", "impact")
    assert reached(result) == [("b", 1)]
    assert [(c["from"]["key"], c["to"]["key"]) for c in result["cycles"]] == [("a", "b")]


async def test_retired_links_are_not_walked(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("echo", "shikigami"), ("shikigami", "temporal"))
    async with AsyncSession(db) as session:
        await write_as(session, "svc+anubis", "nightly verdict: gone", verdict=True)
        await run_sql(
            session,
            "update relation set status = 'retired' where from_item = (select id from item where key = 'shikigami')",
        )
        await session.commit()
    assert reached(await walk(client, db, "echo", "dependencies")) == [("shikigami", 1)]


async def test_items_lookup_and_404(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("echo", "shikigami"))
    [found] = (await client.get("/v1/items", params={"type": "app", "key": "echo"})).json()
    assert (await client.get(f"/v1/items/{found['id']}")).json()["key"] == "echo"
    assert [i["key"] for i in (await client.get("/v1/items", params={"type": "app"})).json()] == ["echo", "shikigami"]
    missing = await client.get("/v1/items/00000000-0000-0000-0000-000000000000/web")
    assert (missing.status_code, missing.headers["content-type"]) == (404, "application/problem+json")


async def test_both_sides_at_once(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("echo", "shikigami"), ("shikigami", "temporal"), ("temporal", "temporal-db"))
    both = await get_web(client, db, "temporal")
    assert reached({"items": both["impact"]}) == [("shikigami", 1), ("echo", 2)]
    assert reached({"items": both["dependencies"]}) == [("temporal-db", 1)]
    only = await get_web(client, db, "temporal", direction="impact")
    assert only["dependencies"] == []


async def test_both_sides_report_the_links_that_close_the_loop(client: AsyncClient, db: AsyncEngine) -> None:
    await web(client, ("a", "b"), ("b", "a"))
    cycles = [(c["from"]["key"], c["to"]["key"]) for c in (await get_web(client, db, "a"))["cycles"]]
    assert sorted(cycles) == [("a", "b"), ("b", "a")]  # each side closes it with a different link, each listed once
