from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

# excerpt 정책: plan.md 의 "server-side truncate" 규칙대로 500자 초과는 잘라낼 뿐 거부하지 않는다.

AUTH_HEADERS = {"X-Webhook-Secret": "test"}
OPENCLAW_URL = "http://127.0.0.1:18789/hooks/agent"


def test_missing_title_returns_400_with_field(client: TestClient) -> None:
    response = client.post(
        "/webhook/geeknews",
        json={"url": "https://example.com/post/1"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert response.json() == {"error": "title"}


def test_missing_url_returns_400_with_field(client: TestClient) -> None:
    response = client.post(
        "/webhook/geeknews",
        json={"title": "Sample"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert response.json() == {"error": "url"}


@respx.mock
def test_excerpt_over_500_chars_is_truncated_not_rejected(client: TestClient) -> None:
    # PR-04 이후 정상 경로는 OpenClaw 호출이 일어나므로 respx 로 가로챈다.
    respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(200, json={"text": "ok", "deliver_ok": True}),
    )
    response = client.post(
        "/webhook/geeknews",
        json={
            "title": "Sample",
            "url": "https://example.com/post/1",
            "excerpt": "x" * 600,
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "matched"
