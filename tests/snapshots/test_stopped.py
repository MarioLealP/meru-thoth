"""An app off on purpose (scaled to 0): the cluster scan says `running: false`, Thoth calls it stopped - seen, present,
never mistaken for an onboarding that stalled or an app that vanished."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.core.test_retire_steps import SHIKIGAMI, step
from tests.core.test_verdicts import ANUBIS
from tests.snapshots.test_snapshots import history, item, put
from thoth.models import Status

FACTORIO = {"type": "app", "key": "factorio", "name": "factorio", "attributes": {"template": "nuwa/upstream"}}
WORKLOAD = {"type": "workload", "key": "clockmost/factorio/deployment/factorio", "name": "factorio", "attributes": {}}


def scan(client: TestClient, running: bool | None, *, send: bool = True) -> int:
    """One cluster scan that sees factorio; `send=False` is a scanner from before the field existed."""
    attributes: dict[str, Any] = {"running": running} if send else {}
    app = {"type": "app", "key": "factorio", "name": "factorio", "attributes": attributes}
    return put(client, "k8s-scan", "clockmost", WORKLOAD, app).status_code


def test_a_declared_app_seen_scaled_to_zero_is_stopped_not_planned(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", FACTORIO)
    assert scan(client, running=False) == 200
    factorio = item(db, "app", "factorio")
    assert (factorio.status, factorio.attributes) == (Status.STOPPED, {"template": "nuwa/upstream", "running": False})


def test_starting_and_stopping_moves_between_active_and_stopped(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", FACTORIO)
    statuses = []
    for running in (False, True, False, None):
        scan(client, running)
        statuses.append(item(db, "app", "factorio").status)
    # null: the scan can't tell (a host binary) - seen is active
    assert statuses == [Status.STOPPED, Status.ACTIVE, Status.STOPPED, Status.ACTIVE]


def test_a_scanner_that_predates_the_field_is_still_accepted(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", FACTORIO)
    assert scan(client, running=None, send=False) == 200  # lenient: Heimdall and Thoth ship in any order
    assert item(db, "app", "factorio").status == Status.ACTIVE


def test_the_running_field_is_the_cluster_scans_alone(client: TestClient) -> None:
    claimed = {**FACTORIO, "attributes": {"template": "nuwa/upstream", "running": False}}
    assert put(client, "shikigami", "all", claimed).status_code == 422


def test_a_stopped_app_that_vanishes_is_queued_and_can_go_missing(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", FACTORIO)
    scan(client, running=False)
    for _ in range(3):
        put(client, "k8s-scan", "clockmost")
    queued = client.get("/v1/unseen", headers=ANUBIS).json()
    assert [(e["item"]["key"], e["item"]["status"]) for e in queued if e["item"]["type"] == "app"] == [
        ("factorio", "stopped")
    ]
    body = {"type": "app", "key": "factorio", "status": "missing", "reason": "gone: app-factorio answered 404"}
    response = client.post("/v1/verdicts", json=body, headers=ANUBIS)
    assert response.status_code == 200, response.text
    assert item(db, "app", "factorio").status == Status.MISSING


def test_the_retire_pass_can_take_down_a_stopped_app(client: TestClient, db: Engine) -> None:
    put(client, "shikigami", "all", FACTORIO)
    scan(client, running=False)
    assert step(client, "decommissioning", app="factorio", headers=SHIKIGAMI).json()["status"] == "decommissioning"
    scan(client, running=False)  # a scan while it's going down changes nothing
    factorio = item(db, "app", "factorio")
    assert factorio.status == Status.DECOMMISSIONING
    assert [v.status for v in history(db, factorio)][-1] == Status.STOPPED
