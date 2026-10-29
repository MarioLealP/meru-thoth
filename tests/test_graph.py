"""Thoth builds the web from one-hop links (D55): chains, diamonds, loops, depth, retired links."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlmodel import Session

from tests.test_relations import link, rel
from tests.test_snapshots import item, put
from thoth.db import write_as


def app(name: str) -> dict[str, Any]:
    return {"type": "app", "key": name, "name": name, "attributes": {"template": None}}


def web(client: TestClient, *links: tuple[str, str]) -> None:
    """Declare apps and `a uses b` links the way Shikigami would."""
    names = sorted({n for pair in links for n in pair})
    put(client, "shikigami", "all", *(app(n) for n in names))
    assert link(client, "shikigami", "all", *(rel("uses", app(a), app(b)) for a, b in links)).is_success


def walk(client: TestClient, db: Engine, name: str, direction: str, depth: int = 10) -> dict[str, Any]:
    """One side of the web, from GET /web?direction=."""
    web_ = get_web(client, db, name, depth=depth, direction=direction)
    return {"items": web_[direction], "cycles": web_["cycles"]}


def get_web(client: TestClient, db: Engine, name: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/v1/items/{item(db, 'app', name).id}/web", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def reached(result: dict[str, Any]) -> list[tuple[str, int]]:
    return [(s["item"]["key"], s["depth"]) for s in result["items"]]


def test_a_chain_both_ways(client: TestClient, db: Engine) -> None:
    web(client, ("echo", "shikigami"), ("shikigami", "temporal"), ("temporal", "temporal-db"))
    assert reached(walk(client, db, "temporal-db", "impact")) == [("temporal", 1), ("shikigami", 2), ("echo", 3)]
    assert reached(walk(client, db, "echo", "dependencies")) == [("shikigami", 1), ("temporal", 2), ("temporal-db", 3)]


def test_depth_one_is_direct_neighbours(client: TestClient, db: Engine) -> None:
    web(client, ("echo", "shikigami"), ("shikigami", "temporal"))
    assert reached(walk(client, db, "echo", "dependencies", depth=1)) == [("shikigami", 1)]


def test_the_link_it_came_by(client: TestClient, db: Engine) -> None:
    web(client, ("echo", "shikigami"))
    [step] = walk(client, db, "shikigami", "impact")["items"]
    assert (step["via"]["from"]["key"], step["via"]["type"], step["via"]["to"]["key"]) == ("echo", "uses", "shikigami")


def test_a_diamond_lists_each_item_once(client: TestClient, db: Engine) -> None:
    web(client, ("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"))
    assert reached(walk(client, db, "a", "dependencies")) == [("b", 1), ("c", 1), ("d", 2)]


def test_a_loop_is_reported_not_followed(client: TestClient, db: Engine) -> None:
    web(client, ("a", "b"), ("b", "c"), ("c", "a"))
    result = walk(client, db, "a", "dependencies", depth=50)
    assert reached(result) == [("b", 1), ("c", 2)]  # a itself is the start, not repeated
    assert [(c["from"]["key"], c["to"]["key"]) for c in result["cycles"]] == [("c", "a")]


def test_two_apps_using_each_other(client: TestClient, db: Engine) -> None:
    web(client, ("a", "b"), ("b", "a"))
    result = walk(client, db, "a", "impact")
    assert reached(result) == [("b", 1)]
    assert [(c["from"]["key"], c["to"]["key"]) for c in result["cycles"]] == [("a", "b")]


def test_retired_links_are_not_walked(client: TestClient, db: Engine) -> None:
    web(client, ("echo", "shikigami"), ("shikigami", "temporal"))
    with Session(db) as session:
        write_as(session, "svc+anubis", "nightly verdict: gone", verdict=True)
        session.exec(
            text(
                "update relation set status = 'retired' where from_item = (select id from item where key = 'shikigami')"
            )
        )  # ty: ignore[no-matching-overload]
        session.commit()
    assert reached(walk(client, db, "echo", "dependencies")) == [("shikigami", 1)]


def test_items_lookup_and_404(client: TestClient, db: Engine) -> None:
    web(client, ("echo", "shikigami"))
    [found] = client.get("/v1/items", params={"type": "app", "key": "echo"}).json()
    assert client.get(f"/v1/items/{found['id']}").json()["key"] == "echo"
    assert [i["key"] for i in client.get("/v1/items", params={"type": "app"}).json()] == ["echo", "shikigami"]
    missing = client.get("/v1/items/00000000-0000-0000-0000-000000000000/web")
    assert (missing.status_code, missing.headers["content-type"]) == (404, "application/problem+json")


def test_both_sides_at_once(client: TestClient, db: Engine) -> None:
    web(client, ("echo", "shikigami"), ("shikigami", "temporal"), ("temporal", "temporal-db"))
    both = get_web(client, db, "temporal")
    assert reached({"items": both["impact"]}) == [("shikigami", 1), ("echo", 2)]
    assert reached({"items": both["dependencies"]}) == [("temporal-db", 1)]
    only = get_web(client, db, "temporal", direction="impact")
    assert only["dependencies"] == []


def test_both_sides_report_the_links_that_close_the_loop(client: TestClient, db: Engine) -> None:
    web(client, ("a", "b"), ("b", "a"))
    cycles = [(c["from"]["key"], c["to"]["key"]) for c in get_web(client, db, "a")["cycles"]]
    assert sorted(cycles) == [("a", "b"), ("b", "a")]  # each side closes it with a different link, each listed once
