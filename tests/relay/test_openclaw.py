"""OpenClaw 호출 경로 단위테스트 — respx 로 외부 호출을 가로채 격리.

plan.md §"Hook Invocation Pattern" / §"Agent Behavior Spec" 의 happy / no_reply / 5xx 재시도 /
5xx 고갈 / Slack 실패 5가지 경로를 모두 회귀 방지.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

OPENCLAW_URL = "http://127.0.0.1:18789/hooks/agent"

VALID_PAYLOAD = {
    "title": "Sample GeekNews Post",
    "url": "https://news.hada.io/topic?id=12345",
    "excerpt": "Some excerpt about an LLM agent framework",
}
AUTH_HEADERS = {"X-Webhook-Secret": "test"}


@respx.mock
def test_happy_path_matched(client: TestClient) -> None:
    """OpenClaw 가 200 matched 응답을 주면 relay 도 200 matched."""
    route = respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(
            200,
            json={"text": "[GeekNews Match]\n제목: ...", "deliver_ok": True},
        )
    )

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "matched", "openclaw_status": 200}
    assert route.call_count == 1


@respx.mock
def test_no_reply_path(client: TestClient) -> None:
    """OpenClaw body 가 NO_REPLY 토큰만 포함하면 relay 는 200 no_reply."""
    route = respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(200, json={"text": "NO_REPLY"}),
    )

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "no_reply", "openclaw_status": 200}
    assert route.call_count == 1


@respx.mock
def test_5xx_then_200_retries_once(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """첫 시도가 5xx 면 1회 재시도 — 두 번째가 200 이면 matched 로 응답."""
    # 백오프 sleep 을 우회해 테스트 속도를 유지한다.
    monkeypatch.setattr("relay.openclaw_client.asyncio.sleep", AsyncMock())
    route = respx.post(OPENCLAW_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"text": "ok", "deliver_ok": True}),
        ],
    )

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "matched", "openclaw_status": 200}
    # 첫 5xx + 재시도 = 2회 호출.
    assert route.call_count == 2


@respx.mock
def test_5xx_twice_returns_502(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1회 재시도 후에도 5xx 면 relay 는 502 openclaw."""
    monkeypatch.setattr("relay.openclaw_client.asyncio.sleep", AsyncMock())
    route = respx.post(OPENCLAW_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(503)],
    )

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 502
    assert response.json() == {"error": "openclaw"}
    assert route.call_count == 2


@respx.mock
def test_slack_delivered_false_returns_503(client: TestClient) -> None:
    """OpenClaw 는 받았지만 deliver_ok=False — Slack 단계 실패는 503 으로 분리."""
    route = respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(
            200,
            json={"text": "[GeekNews Match]\n제목: ...", "deliver_ok": False},
        )
    )

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 503
    assert response.json() == {"error": "slack"}
    assert route.call_count == 1


@respx.mock
def test_transport_error_returns_502_without_retry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport 단계 실패(timeout 등)는 재시도 대상이 아니다 — 즉시 502."""
    # 재시도가 잘못 발생하더라도 테스트가 느려지지 않게 sleep 을 가로챈다.
    monkeypatch.setattr("relay.openclaw_client.asyncio.sleep", AsyncMock())
    route = respx.post(OPENCLAW_URL).mock(side_effect=httpx.ReadTimeout("simulated timeout"))

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 502
    assert response.json() == {"error": "openclaw"}
    # Transport error 는 1회 호출 후 즉시 raise — 5xx 만 재시도 대상.
    assert route.call_count == 1


@respx.mock
def test_4xx_response_returns_502_without_retry(client: TestClient) -> None:
    """OpenClaw 4xx 는 설정/요청 오류로 간주, 재시도 없이 502 매핑."""
    route = respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"}),
    )

    response = client.post("/webhook/geeknews", json=VALID_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 502
    assert response.json() == {"error": "openclaw"}
    assert route.call_count == 1
