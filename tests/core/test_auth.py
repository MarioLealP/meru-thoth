"""Accepting a token (security standard section 5) and the permissions behind each route (D56)."""

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx2 import AsyncClient

from tests.snapshots.test_snapshots import DESKTOP, item
from tests.tokens import bearer, mint
from thoth.core.auth import READ, report

SCAN = "/v1/scans/s1/host-scan/clockmost-desktop/items"


async def test_no_token(anon: AsyncClient) -> None:
    response = await anon.get("/v1/items")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"


async def test_probes_need_no_token(anon: AsyncClient) -> None:
    assert (await anon.get("/heartbeat")).status_code == 200
    assert (await anon.get("/ready")).status_code == 200


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        mint(READ, exp=int(time.time()) - 3600),  # expired
        mint(READ, nbf=int(time.time()) + 3600),  # not yet valid
        mint(READ, iss="https://evil.test/"),  # another issuer
        mint(READ, aud="argocd"),  # a token for another service
        mint(READ, kid="other-key"),  # a key Thoth doesn't know
        mint(READ, key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),  # forged signature
        mint(READ, key="a-shared-secret-that-is-at-least-32-bytes", algorithm="HS256"),  # algorithm not allowed
        mint(READ, sub=None),  # a required claim missing
    ],
    ids=["garbage", "expired", "not-yet", "issuer", "audience", "unknown-key", "forged", "hs256", "no-sub"],
)
async def test_tokens_thoth_does_not_accept(anon: AsyncClient, token: str) -> None:
    response = await anon.get("/v1/items", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "the token isn't accepted"  # the reason is logged, not told


async def test_read_only_cannot_report(anon: AsyncClient) -> None:
    assert (await anon.get("/v1/items", headers=bearer(READ))).status_code == 200
    response = await anon.put(SCAN, json={"items": [DESKTOP]}, headers=bearer(READ))
    assert (response.status_code, response.json()["detail"]) == (403, "needs thoth.report:host-scan@clockmost-desktop")


async def test_a_scanner_reports_only_its_own_scope(anon: AsyncClient) -> None:
    desktop = bearer(report("host-scan", "clockmost-desktop"))
    assert (await anon.put(SCAN, json={"items": [DESKTOP]}, headers=desktop)).status_code == 200
    clockmost = {**DESKTOP, "key": "clockmost", "name": "clockmost"}
    other = await anon.put("/v1/scans/s1/host-scan/clockmost/items", json={"items": [clockmost]}, headers=desktop)
    assert other.status_code == 403  # the desktop's login can't speak for clockmost


async def test_reading_needs_read(anon: AsyncClient) -> None:
    scanner = bearer(report("host-scan", "clockmost-desktop"))
    assert (await anon.get("/v1/items", headers=scanner)).status_code == 403
    assert (await anon.get("/v1/catalog", headers=scanner)).status_code == 403


async def test_changes_are_recorded_under_the_bot(anon: AsyncClient, db) -> None:
    desktop = bearer(report("host-scan", "clockmost-desktop"), name="svc+host-scan-desktop")
    await anon.put(SCAN, json={"items": [DESKTOP]}, headers=desktop)
    assert (await item(db, "host", "clockmost-desktop")).changed_by == "svc+host-scan-desktop"
