"""Reading the past (D39): an item's versions, and the inventory as of a moment."""

import itertools
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.test_snapshots import DESKTOP, item, put


def as_of(client: TestClient, at: datetime, **filters: Any) -> list[tuple[str, str]]:
    response = client.get("/v1/items", params={"as_of": at.isoformat(), **filters})
    assert response.status_code == 200, response.text
    return [(i["key"], i["attributes"]["os"]) for i in response.json()]


def three_versions(client: TestClient) -> list[datetime]:
    """Created, upgraded, renamed - with a moment captured after each."""
    moments = [datetime.now(UTC)]
    put(client, "host-scan", "clockmost-desktop", DESKTOP)
    moments.append(datetime.now(UTC))
    put(client, "host-scan", "clockmost-desktop", {**DESKTOP, "attributes": {"kind": "physical", "os": "CachyOS 2"}})
    moments.append(datetime.now(UTC))
    client.post("/v1/renames", json={"type": "host", "key": "clockmost-desktop", "new_key": "battlestation",
                                     "by": "manual", "reason": "new name"})  # fmt: skip
    moments.append(datetime.now(UTC))
    return moments


def test_versions_newest_first_with_who_and_why(client: TestClient, db: Engine) -> None:
    three_versions(client)
    response = client.get(f"/v1/items/{item(db, 'host', 'battlestation').id}/history")
    versions = response.json()
    assert [(v["key"], v["attributes"]["os"], v["changed_by"]) for v in versions] == [
        ("battlestation", "CachyOS 2", "manual"),
        ("clockmost-desktop", "CachyOS 2", "host-scan"),
        ("clockmost-desktop", "CachyOS", "host-scan"),
    ]
    assert versions[0]["valid_to"] is None
    for newer, older in itertools.pairwise(versions):
        assert older["valid_to"] == newer["valid_from"]  # no gaps, no overlaps


def test_as_of_each_moment(client: TestClient) -> None:
    before, created, upgraded, renamed = three_versions(client)
    assert as_of(client, before) == []  # didn't exist yet
    assert as_of(client, created) == [("clockmost-desktop", "CachyOS")]
    assert as_of(client, upgraded) == [("clockmost-desktop", "CachyOS 2")]
    assert as_of(client, renamed) == [("battlestation", "CachyOS 2")]


def test_as_of_filters_on_the_old_values(client: TestClient) -> None:
    _, created, _, _ = three_versions(client)
    assert as_of(client, created, key="clockmost-desktop") == [("clockmost-desktop", "CachyOS")]
    assert as_of(client, created, key="battlestation") == []


def test_history_of_an_unknown_item(client: TestClient) -> None:
    response = client.get("/v1/items/00000000-0000-0000-0000-000000000000/history")
    assert (response.status_code, response.headers["content-type"]) == (404, "application/problem+json")
