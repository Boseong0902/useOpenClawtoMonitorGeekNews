from __future__ import annotations

from fastapi.testclient import TestClient

VALID_PAYLOAD = {"title": "Sample", "url": "https://example.com/post/1"}


def test_missing_secret_returns_401(client: TestClient) -> None:
    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD)
    assert response.status_code == 401
    assert response.json() == {"error": "auth"}


def test_wrong_secret_returns_401(client: TestClient) -> None:
    response = client.post(
        "/webhook/geeknews",
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": "not-the-real-secret"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "auth"}


def test_correct_secret_returns_200(client: TestClient) -> None:
    response = client.post(
        "/webhook/geeknews",
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": "test"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "matched"
