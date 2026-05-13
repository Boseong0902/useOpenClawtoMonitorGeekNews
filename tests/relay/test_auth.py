from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

VALID_PAYLOAD = {"title": "Sample", "url": "https://example.com/post/1"}
OPENCLAW_URL = "http://127.0.0.1:18789/hooks/agent"


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


@respx.mock
def test_correct_secret_returns_200(client: TestClient) -> None:
    # PR-04 이후 경로는 실제 OpenClaw 를 호출하므로 respx 로 가로채야 한다.
    respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(200, json={"text": "ok", "deliver_ok": True}),
    )
    response = client.post(
        "/webhook/geeknews",
        json=VALID_PAYLOAD,
        headers={"X-Webhook-Secret": "test"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "matched"
