from __future__ import annotations

from fastapi.testclient import TestClient

# excerpt 정책: plan.md 의 "server-side truncate" 규칙대로 500자 초과는 잘라낼 뿐 거부하지 않는다.

AUTH_HEADERS = {"X-Webhook-Secret": "test"}


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


def test_excerpt_over_500_chars_is_truncated_not_rejected(client: TestClient) -> None:
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
