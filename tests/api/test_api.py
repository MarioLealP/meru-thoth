from fastapi.testclient import TestClient

from thoth.api import app

client = TestClient(app)


def test_heartbeat() -> None:
    response = client.get("/heartbeat")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_errors_are_problem_details() -> None:
    response = client.get("/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found"}
