"""Shikigami's retire pass in Thoth (D85): decommissioning, then retained - only with thoth.retire, enforced twice."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

from tests.test_snapshots import history, item, put
from tests.tokens import bearer
from thoth.auth import READ, RETIRE, report
from thoth.db import write_as
from thoth.models import Status

SHIKIGAMI = bearer(RETIRE, report("shikigami", "all"), name="shikigami")
KITSUNE = {"type": "app", "key": "kitsune", "name": "kitsune", "attributes": {"template": "nuwa/python"}}


def step(client: TestClient, status: str, app: str = "kitsune", headers: dict[str, str] = SHIKIGAMI) -> Any:
    return client.post(
        "/v1/retire-steps", json={"app": app, "status": status, "reason": "requests#13"}, headers=headers
    )


def test_an_app_goes_decommissioning_then_retained(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", KITSUNE)
    first = step(client, "decommissioning")
    assert first.status_code == 200, first.text
    assert (first.json()["status"], first.json()["changed_by"]) == ("decommissioning", "shikigami")
    assert first.json()["change_reason"] == "decommissioning: requests#13"
    assert step(client, "retained").json()["status"] == "retained"
    kitsune = item(db, "app", "kitsune")
    assert [v.status for v in history(db, kitsune)] == [Status.PLANNED, Status.DECOMMISSIONING]


def test_a_retried_step_is_answered_as_it_is(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", KITSUNE)
    step(client, "decommissioning")
    again = step(client, "decommissioning")
    assert (again.status_code, again.json()["status"]) == (200, "decommissioning")
    assert len(history(db, item(db, "app", "kitsune"))) == 1  # no new version


def test_steps_go_in_order_and_never_back(client: TestClient) -> None:
    put(client, "shikigami", "all", KITSUNE)
    early = step(client, "retained")
    assert early.status_code == 409
    assert "is planned: retained comes after" in early.json()["detail"]
    step(client, "decommissioning")
    step(client, "retained")
    assert step(client, "decommissioning").status_code == 409


def test_seeing_a_retiring_app_does_not_bring_it_back(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", KITSUNE)
    step(client, "decommissioning")
    seen = {"type": "app", "key": "kitsune", "name": "kitsune", "attributes": {}}
    assert put(client, "k8s-scan", "clockmost", seen).status_code == 200  # a pod still stopping
    assert item(db, "app", "kitsune").status == Status.DECOMMISSIONING


def test_refusals(client: TestClient) -> None:
    put(client, "shikigami", "all", KITSUNE)
    assert step(client, "decommissioning", app="nope").status_code == 404
    assert step(client, "retired").status_code == 422  # only Anubis retires, and not here
    for headers in (bearer(READ), bearer(report("shikigami", "all"))):
        response = step(client, "decommissioning", headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "needs thoth.retire"


@pytest.mark.parametrize("status", ["decommissioning", "retained"])
def test_the_database_refuses_it_without_the_retire_flag(client: TestClient, db: Engine, status: str) -> None:
    put(client, "shikigami", "all", KITSUNE)
    with Session(db) as session:
        write_as(session, "manual", "trying it by hand")
        with pytest.raises(DBAPIError, match="only the retire pass sets"):
            session.exec(text(f"update item set status = '{status}'"))  # ty: ignore[no-matching-overload]
