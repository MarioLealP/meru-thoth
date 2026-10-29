from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from tests.test_relations import BENYLIN, MEMBER, WHEEL, link
from tests.test_snapshots import DESKTOP, history, item, put
from tests.tokens import bearer
from thoth.auth import READ, report
from thoth.models import Relation


def rename(client: TestClient, headers: dict[str, str] | None = None, /, **body: Any) -> Any:
    request = {"type": "host", "key": "clockmost-desktop", "new_key": "battlestation", "reason": "new name", **body}
    return client.post("/v1/renames", json=request, headers=headers)


def test_rename_keeps_the_item_and_its_history(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    before = item(db, "host", "clockmost-desktop")
    response = rename(client, new_name="battlestation")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["key"], body["name"]) == (str(before.id), "battlestation", "battlestation")
    assert body["change_reason"] == "rename clockmost-desktop -> battlestation: new name"
    [old] = history(db, item(db, "host", "battlestation"))
    assert old.key == "clockmost-desktop"


def test_links_follow_the_renamed_item(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    link(client, "host-scan", "clockmost-desktop", MEMBER)
    rename(client, bearer(report("host-scan", "clockmost-desktop")), type="group", key=WHEEL["key"],
           new_key="linux/clockmost-desktop/admins")  # its own source may rename it  # fmt: skip
    with Session(db) as session:
        [row] = session.exec(select(Relation)).all()
    assert row.to_item == item(db, "group", "linux/clockmost-desktop/admins").id


def test_refusals(client: TestClient) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    put(client, "host-scan", "clockmost", {**DESKTOP, "key": "clockmost", "name": "clockmost"})
    cases = [
        (dict(key="nope"), 404, "no host nope"),
        (dict(new_key="Bad Key"), 422, "doesn't match"),
        (dict(new_key="clockmost"), 409, "already exists"),
        (dict(type="toaster"), 422, "unknown type"),
    ]
    for body, status, detail in cases:
        response = rename(client, **body)
        assert (response.status_code, response.headers["content-type"]) == (status, "application/problem+json")
        assert detail in response.json()["detail"], body
    for headers in (bearer(report("authentik-sync", "all")), bearer(READ)):  # another source; read-only
        response = rename(client, headers)
        assert response.status_code == 403
        assert "needs thoth.manual, or to report as host-scan" in response.json()["detail"]
