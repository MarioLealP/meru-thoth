"""Item snapshots through the API, against the real database: D51 (strict slices), D52 (scope), D53/D54 (misses)."""

import itertools
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine
from sqlmodel import Session, select

from tests.conftest import run_sql
from thoth.db.session import write_as
from thoth.models import Item, ItemHistory, Status

DESKTOP = {"type": "host", "key": "clockmost-desktop", "name": "clockmost-desktop",
           "attributes": {"kind": "physical", "os": "CachyOS"}}  # fmt: skip


def account(login: str, email: str | None = None, host: str = "clockmost-desktop") -> dict[str, Any]:
    return {"type": "account", "key": f"linux/{host}/{login}", "name": login, "attributes": {"email": email}}


_scans = itertools.count()


def put(client: TestClient, source: str, scope: str, *items: dict[str, Any], scan: str | None = None) -> Response:
    """Phase 1 of a scan; a new scan id each call unless one is given."""
    return client.put(f"/v1/scans/{scan or f'scan-{next(_scans)}'}/{source}/{scope}/items", json={"items": list(items)})


def item(db: Engine, type_name: str, key: str) -> Item:
    with Session(db) as session:
        return session.exec(select(Item).where(Item.type == type_name, Item.key == key)).one()


def history(db: Engine, it: Item) -> list[ItemHistory]:
    with Session(db) as session:
        return list(session.exec(select(ItemHistory).where(ItemHistory.id == it.id)))


def test_first_snapshot_creates_items(client: TestClient, db: Engine) -> None:
    response = put(client, "host-scan", "clockmost-desktop", DESKTOP, account("benylin", "lealmariop@gmail.com"))
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 2, "changed": 0, "unchanged": 0, "missed": 0, "for_anubis": [], "skipped": []}
    host = item(db, "host", "clockmost-desktop")
    assert (host.status, host.source, host.attributes) == (Status.ACTIVE, "host-scan", DESKTOP["attributes"])
    assert host.change_reason == "snapshot from host-scan@clockmost-desktop"


def test_same_snapshot_again_changes_nothing(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    first = item(db, "host", "clockmost-desktop")
    assert put(client, "host-scan", "clockmost-desktop", DESKTOP).json()["unchanged"] == 1
    again = item(db, "host", "clockmost-desktop")
    assert again.last_seen > first.last_seen
    assert history(db, again) == []  # last_seen moving is not a version


def test_a_changed_value_is_a_new_version(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    upgraded = {**DESKTOP, "attributes": {"kind": "physical", "os": "CachyOS 2026.11"}}
    assert put(client, "host-scan", "clockmost-desktop", upgraded).json()["changed"] == 1
    [old] = history(db, item(db, "host", "clockmost-desktop"))
    assert old.attributes["os"] == "CachyOS"


def test_a_missing_field_refuses_the_whole_snapshot(client: TestClient, db: Engine) -> None:
    broken = {**DESKTOP, "attributes": {"kind": "physical"}}  # os missing: the scanner broke (D51)
    response = put(client, "host-scan", "clockmost-desktop", account("benylin"), broken)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"] == [{"msg": "items[1] (host clockmost-desktop): os: Field required"}]
    with Session(db) as session:  # nothing applied, not even the valid account
        assert session.exec(select(Item)).all() == []
    [run] = client.get("/v1/snapshots").json()
    assert (run["accepted"], run["summary"]["errors"]) == (
        False,
        ["items[1] (host clockmost-desktop): os: Field required"],
    )


def test_null_is_a_value(client: TestClient) -> None:
    assert put(
        client, "host-scan", "clockmost-desktop", {**DESKTOP, "attributes": {"kind": "vm", "os": None}}
    ).is_success


def test_a_field_the_source_does_not_own_is_refused(client: TestClient) -> None:
    sneaky = {"type": "person", "key": "mario", "name": "Mario", "attributes": {"names": [], "emails": []}}
    response = put(client, "authentik-sync", "all", sneaky)  # person fields belong to manual
    assert response.status_code == 422
    assert "names: Extra inputs are not permitted" in response.json()["errors"][0]["msg"]


def test_items_outside_the_scope_are_refused(client: TestClient) -> None:
    response = put(client, "host-scan", "clockmost-desktop", account("claude-admin", host="clockmost"))
    assert response.status_code == 422
    assert response.json()["errors"][0]["msg"].endswith("outside scope clockmost-desktop")


def test_whole_system_sources_use_scope_all(client: TestClient) -> None:
    response = put(client, "authentik-sync", "clockmost")
    assert response.status_code == 422
    assert "its scope is 'all'" in response.json()["errors"][0]["msg"]


def test_a_whole_system_source_only_reports_its_types(client: TestClient) -> None:
    host = {"type": "host", "key": "made-up", "name": "made-up", "attributes": {}}
    app = {"type": "app", "key": "echo", "name": "echo", "attributes": {"template": "nuwa/python"}}
    response = put(client, "shikigami", "all", app, host)
    assert response.status_code == 422  # all or nothing: the app isn't written either
    assert [e["msg"] for e in response.json()["errors"]] == ["items[1] (host made-up): outside scope all"]
    assert put(client, "shikigami", "all", app).status_code == 200


def test_bad_keys_and_unknown_types_are_refused(client: TestClient) -> None:
    response = put(client, "host-scan", "clockmost-desktop", {**DESKTOP, "key": "Clockmost-Desktop"},
                   {"type": "toaster", "key": "x", "name": "x", "attributes": {}})  # fmt: skip
    messages = [e["msg"] for e in response.json()["errors"]]
    assert any("key doesn't match" in m for m in messages)
    assert any("unknown type" in m for m in messages)


def test_three_misses_go_to_anubis_and_coming_back_resets(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, account("olduser"))
    for expected in ([], [], ["account linux/clockmost-desktop/olduser"]):
        response = put(client, "host-scan", "clockmost-desktop", DESKTOP).json()
        assert (response["missed"], response["for_anubis"]) == (1, expected)
    assert item(db, "account", "linux/clockmost-desktop/olduser").status == Status.ACTIVE  # only Anubis decides
    put(client, "host-scan", "clockmost-desktop", DESKTOP, account("olduser"))
    assert put(client, "host-scan", "clockmost-desktop", DESKTOP).json()["for_anubis"] == []  # count restarted


def test_scopes_do_not_miss_each_other(client: TestClient) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    clockmost = {**DESKTOP, "key": "clockmost", "name": "clockmost"}
    assert put(client, "host-scan", "clockmost", clockmost).json()["missed"] == 0
    assert put(client, "host-scan", "clockmost-desktop", DESKTOP).json()["missed"] == 0


def test_declared_is_planned_until_seen_and_declaration_wins(client: TestClient, db: Engine) -> None:
    route = {"type": "route", "key": "echo.clockmost.com", "name": "echo"}
    put(client, "shikigami", "all", {**route, "attributes": {"auth": "authentik"}})
    assert item(db, "route", "echo.clockmost.com").status == Status.PLANNED
    put(client, "caddy-scan", "all", {**route, "attributes": {"auth": "none", "tls": True}})
    seen = item(db, "route", "echo.clockmost.com")
    assert seen.status == Status.ACTIVE
    assert seen.attributes == {"auth": "authentik", "tls": True}  # Shikigami ranks first for auth (D49)
    assert seen.source == "shikigami"  # the first reporter owns the name


def test_seeing_it_never_undoes_a_verdict(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    with Session(db) as session:
        write_as(session, "shikigami", "retire request #1 finished", retire=True)
        run_sql(session, "update item set status = 'decommissioning'")
        session.commit()
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    assert item(db, "host", "clockmost-desktop").status == Status.DECOMMISSIONING  # Anubis decides, not scanners


def test_latest_snapshot_per_source(client: TestClient) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    put(client, "authentik-sync", "all")
    runs = client.get("/v1/snapshots").json()
    assert all(r["kind"] == "items" for r in runs)
    assert [(r["source"], r["scope"], r["accepted"]) for r in runs] == [
        ("authentik-sync", "all", True),
        ("host-scan", "clockmost-desktop", True),
    ]


def test_resending_a_scan_counts_no_extra_misses(client: TestClient) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, account("olduser"))
    for _ in range(3):  # the same scan, retried three times
        response = put(client, "host-scan", "clockmost-desktop", DESKTOP, scan="scan-retried").json()
    assert response["for_anubis"] == []  # one scan without it, not three


def test_things_that_come_and_go_wait_for_time_not_scans(client: TestClient) -> None:
    phone = {"type": "device", "key": "a4:5e:60:12:34:56", "name": "phone", "attributes": {}}
    put(client, "r7000-scan", "all", phone)
    for _ in range(5):  # off the Wi-Fi for five scans: normal for a phone
        assert put(client, "r7000-scan", "all").json()["for_anubis"] == []


def test_the_cluster_scan_confirms_declared_apps_and_never_creates_them(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", {"type": "app", "key": "tanuki", "name": "tanuki",
                                     "attributes": {"template": "nuwa/python"}})  # fmt: skip
    workload = {"type": "workload", "key": "clockmost/tanuki/deployment/tanuki", "name": "tanuki", "attributes": {}}
    stray = {"type": "app", "key": "stray", "name": "stray", "attributes": {}}
    response = put(client, "k8s-scan", "clockmost", workload, stray)
    assert response.status_code == 422  # all or nothing: the workload isn't written either
    assert [e["msg"] for e in response.json()["errors"]] == [
        "items[1] (app stray): not declared - k8s-scan can confirm one, never create it"
    ]
    assert item(db, "app", "tanuki").status == Status.PLANNED

    seen_app = {"type": "app", "key": "tanuki", "name": "tanuki", "attributes": {}}
    assert put(client, "k8s-scan", "clockmost", workload, seen_app).status_code == 200
    tanuki = item(db, "app", "tanuki")
    assert (tanuki.status, tanuki.source, tanuki.attributes) == (
        Status.ACTIVE,
        "shikigami",
        {"template": "nuwa/python"},
    )


def test_the_cluster_scan_sends_no_app_fields(client: TestClient) -> None:
    put(client, "shikigami", "all", {"type": "app", "key": "tanuki", "name": "tanuki",
                                     "attributes": {"template": "nuwa/python"}})  # fmt: skip
    claimed = {"type": "app", "key": "tanuki", "name": "tanuki", "attributes": {"template": "nuwa/rust"}}
    assert put(client, "k8s-scan", "clockmost", claimed).status_code == 422  # the template is Shikigami's
