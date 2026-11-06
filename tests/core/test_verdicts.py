"""Anubis's side of Thoth (D53, D118): the unseen queue and verdicts - `retired` only with thoth.verdict, enforced
twice."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

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


def miss(client: TestClient, times: int) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, OLDUSER)
    for _ in range(times):
        put(client, "host-scan", "clockmost-desktop", DESKTOP)


def verdict(client: TestClient, status: str, key: str = OLDUSER_KEY, headers: dict[str, str] = ANUBIS) -> Any:
    body = {"type": "account", "key": key, "status": status, "reason": "gone: getent passwd olduser found nothing"}
    return client.post("/v1/verdicts", json=body, headers=headers)


def test_three_misses_put_an_item_on_the_unseen_queue(client: TestClient) -> None:
    miss(client, 3)
    response = client.get("/v1/unseen", headers=ANUBIS)
    assert response.status_code == 200, response.text
    [entry] = response.json()
    assert (entry["item"]["key"], entry["source"], entry["scope"], entry["missed_scans"]) == (
        OLDUSER_KEY,
        "host-scan",
        "clockmost-desktop",
        3,
    )


def test_two_misses_are_not_queued(client: TestClient) -> None:
    miss(client, 2)
    assert client.get("/v1/unseen", headers=ANUBIS).json() == []


def test_things_that_come_and_go_are_not_queued_by_scans(client: TestClient) -> None:
    put(client, "r7000-scan", "all", {"type": "device", "key": "a4:5e:60:12:34:56", "name": "phone", "attributes": {}})
    for _ in range(5):
        put(client, "r7000-scan", "all")
    assert client.get("/v1/unseen", headers=ANUBIS).json() == []


def test_a_retiring_item_is_not_queued(client: TestClient, db: Engine) -> None:
    miss(client, 3)
    with Session(db) as session:
        write_as(session, "shikigami", "retire request #1", retire=True)
        run_sql(session, f"update item set status = 'decommissioning' where key = '{OLDUSER_KEY}'")
        session.commit()
    assert client.get("/v1/unseen", headers=ANUBIS).json() == []  # its absence is expected


def test_the_queue_needs_read(anon: TestClient) -> None:
    assert anon.get("/v1/unseen").status_code == 401


def test_retired_leaves_the_queue_and_keeps_the_reason(client: TestClient, db: Engine) -> None:
    miss(client, 3)
    response = verdict(client, "retired")
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["changed_by"]) == ("retired", "anubis")
    assert response.json()["change_reason"] == "retired: gone: getent passwd olduser found nothing"
    assert client.get("/v1/unseen", headers=ANUBIS).json() == []
    assert [v.status for v in history(db, item(db, "account", OLDUSER_KEY))] == [Status.ACTIVE]


def test_missing_then_retired(client: TestClient) -> None:
    miss(client, 3)
    assert verdict(client, "missing").json()["status"] == "missing"
    assert len(client.get("/v1/unseen", headers=ANUBIS).json()) == 1  # still unseen: checked again next pass
    assert verdict(client, "retired").json()["status"] == "retired"


def test_a_verdict_already_given_is_answered_as_it_is(client: TestClient, db: Engine) -> None:
    miss(client, 3)
    verdict(client, "retired")
    again = verdict(client, "retired")
    assert (again.status_code, again.json()["status"]) == (200, "retired")
    assert len(history(db, item(db, "account", OLDUSER_KEY))) == 1  # no new version


def test_retiring_takes_the_links_with_it(client: TestClient, db: Engine) -> None:
    put(client, "host-scan", "clockmost-desktop", DESKTOP, BENYLIN, WHEEL)
    link(client, "host-scan", "clockmost-desktop", MEMBER)
    response = verdict(client, "retired", key=BENYLIN["key"])
    assert response.status_code == 200, response.text
    assert [r.status for r in relations(db)] == [Status.RETIRED]


@pytest.mark.parametrize(
    ("start", "wanted"),
    [("planned", "retired"), ("decommissioning", "retired"), ("missing", "missing"), ("retired", "missing")],
)
def test_verdicts_only_from_their_states(client: TestClient, db: Engine, start: str, wanted: str) -> None:
    miss(client, 3)
    flags = {"retire": True} if start in ("decommissioning",) else {"verdict": start == "retired"}
    with Session(db) as session:
        write_as(session, "test", "set up", **flags)
        run_sql(session, f"update item set status = '{start}' where key = '{OLDUSER_KEY}'")
        session.commit()
    response = verdict(client, wanted)
    if start == wanted:
        assert response.status_code == 200
    else:
        assert response.status_code == 409, response.text
        assert f"is {start}" in response.json()["detail"]


def test_refusals(client: TestClient) -> None:
    miss(client, 3)
    assert verdict(client, "retired", headers=bearer(READ, RETIRE, name="shikigami")).status_code == 403
    assert verdict(client, "retired", key="linux/clockmost-desktop/nobody").status_code == 404
    assert verdict(client, "active").status_code == 422  # Anubis never brings things back; scanners do


def test_the_database_refuses_retired_without_the_verdict_flag(db: Engine, client: TestClient) -> None:
    miss(client, 3)
    with Session(db) as session:
        write_as(session, "someone", "trying")
        with pytest.raises(DBAPIError, match="only anubis retires"):
            run_sql(session, f"update item set status = 'retired' where key = '{OLDUSER_KEY}'")
