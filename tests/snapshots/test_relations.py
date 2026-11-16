"""Relation snapshots (phase 2 of a scan, D54) through the API, against the real database."""

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine
from sqlmodel import Session, select

from tests.snapshots.test_snapshots import DESKTOP, account, put
from thoth.catalog import Source
from thoth.models import Relation
from thoth.snapshots import ReportedItem, apply_items

_scans = itertools.count()
WHEEL = {"type": "group", "key": "linux/clockmost-desktop/wheel", "name": "wheel", "attributes": {}}
LAN = {"type": "network", "key": "r7000/br0", "name": "LAN", "attributes": {"kind": "lan", "cidr": "192.168.10.0/24"}}


def end(item: dict[str, Any]) -> dict[str, str]:
    return {"type": item["type"], "key": item["key"]}


def rel(type_name: str, a: dict[str, Any], b: dict[str, Any], **attributes: Any) -> dict[str, Any]:
    return {"type": type_name, "from": end(a), "to": end(b), "attributes": attributes}


def link(client: TestClient, source: str, scope: str, *relations: dict[str, Any], scan: str | None = None) -> Response:
    url = f"/v1/scans/{scan or f'links-{next(_scans)}'}/{source}/{scope}/relations"
    return client.put(url, json={"relations": list(relations)})


def relations(db: Engine) -> list[Relation]:
    with Session(db) as session:
        return list(session.exec(select(Relation)))


BENYLIN = account("benylin", "lealmariop@gmail.com")
MEMBER = rel("member-of", BENYLIN, WHEEL)  # role belongs to k8s-scan: host-scan sends no attributes


def test_links_between_a_sources_own_items(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    response = link(client, "host-scan", "clockmost-desktop", MEMBER)
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    [row] = relations(db)
    assert (row.type, row.source, row.attributes) == ("member-of", "host-scan", {})


def test_links_to_another_sources_items(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)  # phase 1: both sources' items
    put(client, "r7000-scan", "all", LAN)
    response = link(client, "r7000-scan", "all", rel("attached-to", DESKTOP, LAN, ip="192.168.10.74"))
    assert response.json()["created"] == 1  # phase 2 finds both ends


def test_a_dangling_link_is_skipped_then_heals(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)  # the router's items failed this scan
    attached = rel("attached-to", DESKTOP, LAN, ip="192.168.10.74")
    response = link(client, "r7000-scan", "all", attached).json()
    assert response["skipped"] == [
        "host clockmost-desktop attached-to network r7000/br0 (no item network r7000/br0 yet)"
    ]
    assert relations(db) == []
    put(client, "r7000-scan", "all", LAN)  # next scan: the router's items are back
    assert link(client, "r7000-scan", "all", attached).json()["created"] == 1


def test_a_link_from_outside_the_scope_is_refused(client: TestClient) -> None:
    put(client, "host-scan", "clockmost", {**DESKTOP, "key": "clockmost", "name": "clockmost"},
        account("claude-admin", host="clockmost"))  # fmt: skip
    put(client, "host-scan", "clockmost-desktop", DESKTOP, WHEEL)
    response = link(client, "host-scan", "clockmost-desktop",
                    rel("member-of", account("claude-admin", host="clockmost"), WHEEL))  # fmt: skip
    assert response.status_code == 422
    assert response.json()["errors"][0]["msg"].endswith("its from end is outside scope clockmost-desktop")


def test_wrong_ends_and_missing_fields_are_refused(client: TestClient) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    response = link(
        client,
        "host-scan",
        "clockmost-desktop",
        rel("member-of", DESKTOP, DESKTOP, role=None),  # a host can't be a group; a link to itself
        rel("member-of", BENYLIN, WHEEL),
    )  # role is owned by k8s-scan, not host-scan - fine; see below
    messages = [e["msg"] for e in response.json()["errors"]]
    assert any("doesn't allow host at that end" in m for m in messages)
    assert any("a link to itself" in m for m in messages)
    assert any("role: Extra inputs are not permitted" in m for m in messages)


def test_a_vanished_link_goes_to_anubis_after_three_scans(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    link(client, "host-scan", "clockmost-desktop", MEMBER)
    results = [link(client, "host-scan", "clockmost-desktop").json()["for_anubis"] for _ in range(3)]
    assert results == [
        [],
        [],
        ["account linux/clockmost-desktop/benylin member-of group linux/clockmost-desktop/wheel"],
    ]


def test_the_desktop_off_the_lan_waits_thirty_days(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    put(client, "r7000-scan", "all", LAN)
    link(client, "r7000-scan", "all", rel("attached-to", DESKTOP, LAN, ip="192.168.10.74"))
    for _ in range(5):  # the desktop is off: the router stops listing it
        assert link(client, "r7000-scan", "all").json()["for_anubis"] == []


def test_thirty_days_unseen_does_go_to_anubis(db: Engine) -> None:
    phone = ReportedItem(type="device", key="a4:5e:60:12:34:56", name="phone", attributes={})
    start = datetime.now(UTC)
    with Session(db) as session:
        apply_items(session, "svc+r7000-scan", "s1", Source.ROUTER, "all", [phone], start)
        session.commit()
    with Session(db) as session:
        summary = apply_items(session, "svc+r7000-scan", "s2", Source.ROUTER, "all", [], start + timedelta(days=31))
        session.commit()
    assert summary.for_anubis == ["device a4:5e:60:12:34:56"]


def test_a_link_seen_again_is_the_same_row_and_a_changed_value_updates_it(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    put(client, "r7000-scan", "all", LAN)
    link(client, "r7000-scan", "all", rel("attached-to", DESKTOP, LAN, ip="192.168.10.74"))
    [first] = relations(db)
    same = link(client, "r7000-scan", "all", rel("attached-to", DESKTOP, LAN, ip="192.168.10.74")).json()
    assert (same["created"], same["changed"], same["unchanged"]) == (0, 0, 1)
    moved = link(client, "r7000-scan", "all", rel("attached-to", DESKTOP, LAN, ip="192.168.10.75")).json()
    assert (moved["created"], moved["changed"], moved["unchanged"]) == (0, 1, 0)
    [row] = relations(db)  # still the one row, now with the new value
    assert (row.id, row.attributes["ip"]) == (first.id, "192.168.10.75")
    assert row.last_seen > first.last_seen


def test_ready_checks_links_too(client: TestClient) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    link(client, "host-scan", "clockmost-desktop", MEMBER)
    assert client.get("/ready").status_code == 200
