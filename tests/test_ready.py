from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from thoth.catalog import ITEMS, RELATIONS
from thoth.db import write_as
from thoth.models import Item, ItemObservation, ItemType, RelationType


def add_host(db: Engine, key: str, attributes: dict, observed: dict | None = None) -> None:
    now = datetime.now(UTC)
    with Session(db) as session:
        write_as(session, "test", "setup")
        item = Item(type="host", key=key, name=key, source="host-scan", attributes=attributes, first_seen=now,
                    last_seen=now)  # fmt: skip
        session.add(item)
        session.flush()
        if observed is not None:
            session.add(ItemObservation(item_id=item.id, source="host-scan", scope=key.lower(), attributes=observed,
                                        first_seen=now, last_seen=now))  # fmt: skip
        session.commit()


def test_ready_syncs_the_catalog(client: TestClient, db: Engine) -> None:
    with Session(db) as session:
        session.exec(ItemType.__table__.delete())  # ty: ignore[unresolved-attribute]
        session.commit()
    response = client.get("/ready")
    assert response.status_code == 200, response.text
    with Session(db) as session:
        assert session.exec(select(func.count()).select_from(ItemType)).one() == len(ITEMS)
        assert session.exec(select(func.count()).select_from(RelationType)).one() == len(RELATIONS)


def test_rows_that_fit_are_ready(client: TestClient, db: Engine) -> None:
    add_host(db, "clockmost", {"kind": "physical"}, {"kind": "physical"})
    assert client.get("/ready").status_code == 200


def test_misfit_rows_keep_it_unready(client: TestClient, db: Engine) -> None:
    add_host(db, "Bad_Key", {})
    add_host(db, "clockmost", {"kind": "mainframe"}, {"kind": "mainframe"})
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "host Bad_Key: key doesn't match" in detail
    assert "host clockmost, from host-scan@clockmost: kind:" in detail
    assert client.get("/heartbeat").status_code == 200  # alive, just not ready


def test_catalog_endpoint(client: TestClient) -> None:
    body = client.get("/v1/catalog").json()
    assert body["items"]["route"]["schema"]["properties"]["auth"]["x-owners"] == ["shikigami", "caddy-scan"]
