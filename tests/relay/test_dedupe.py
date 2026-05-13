"""24h dedupe re-check 단위 테스트 — OpenClaw 호출이 정확히 필요한 만큼만 발생함을 증명.

plan.md §"Server-side behavior > dedupe re-check" 의 계약:
- 같은 guid/정규화 URL 이 24h 안에 matched 였으면 OpenClaw 호출 없이 short-circuit.
- 24h 가 지났거나 first-time 이면 정상 OpenClaw 호출.
- no_reply / slack 실패는 seen store 에 기록하지 않으므로 다음 호출이 다시 OpenClaw 를 친다.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient

OPENCLAW_URL = "http://127.0.0.1:18789/hooks/agent"
AUTH_HEADERS = {"X-Webhook-Secret": "test"}

_MATCHED_RESPONSE = httpx.Response(
    200,
    json={"text": "[GeekNews Match]\n제목: ...", "deliver_ok": True},
)


@respx.mock
def test_same_guid_within_24h_skips_openclaw(client: TestClient) -> None:
    """같은 guid 두 번 호출 — 두 번째는 dedupe hit 이고 OpenClaw 는 1회만 호출."""
    route = respx.post(OPENCLAW_URL).mock(return_value=_MATCHED_RESPONSE)

    payload = {
        "title": "Sample",
        "url": "https://news.hada.io/topic?id=12345",
        "guid": "GN-GUID-001",
    }

    first = client.post("/webhook/geeknews", json=payload, headers=AUTH_HEADERS)
    assert first.status_code == 200
    assert first.json() == {"status": "matched", "openclaw_status": 200}

    second = client.post("/webhook/geeknews", json=payload, headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert second.json() == {"status": "no_reply", "reason": "dedupe"}

    # OpenClaw 는 첫 번째 호출에서만 맞아야 한다.
    assert route.call_count == 1


@respx.mock
def test_same_url_different_query_strings_dedupes(client: TestClient) -> None:
    """guid 가 없을 때, query string 만 다른 동일 URL 은 정규화 후 같은 키로 인식."""
    route = respx.post(OPENCLAW_URL).mock(return_value=_MATCHED_RESPONSE)

    first_payload = {
        "title": "Sample",
        "url": "https://news.hada.io/topic?utm_source=a",
    }
    second_payload = {
        "title": "Sample",
        "url": "https://news.hada.io/topic?utm_source=b",
    }

    first = client.post("/webhook/geeknews", json=first_payload, headers=AUTH_HEADERS)
    assert first.status_code == 200
    assert first.json()["status"] == "matched"

    second = client.post("/webhook/geeknews", json=second_payload, headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert second.json() == {"status": "no_reply", "reason": "dedupe"}

    assert route.call_count == 1


# 25h 경과를 흉내내려 SQLite 행을 직접 mutate — 본 함수는 lifespan 의 연결과 다른 connection 을
# 잠시 열어 UPDATE 후 닫는다. SQLite 기본(deferred) 모드에서는 안전하지만, 추후 WAL 활성화 시
# 락 경합이 발생할 수 있음을 인지하고 둔다.
@respx.mock
def test_seen_row_older_than_24h_does_not_dedupe(client: TestClient) -> None:
    """seen_at 이 25시간 전으로 박혀 있으면 dedupe 윈도우 밖 — 두 번째 호출도 OpenClaw 를 친다."""
    route = respx.post(OPENCLAW_URL).mock(return_value=_MATCHED_RESPONSE)

    payload = {
        "title": "Sample",
        "url": "https://news.hada.io/topic?id=99",
        "guid": "GN-GUID-OLD",
    }

    first = client.post("/webhook/geeknews", json=payload, headers=AUTH_HEADERS)
    assert first.status_code == 200

    # SQLite 의 seen_at 을 25시간 전으로 강제 업데이트 — 시간 흐름을 시뮬레이션.
    db_path = os.environ["RELAY_SEEN_DB_PATH"]
    old_seen_at = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE seen SET seen_at = ? WHERE key = ?",
            (old_seen_at, "GN-GUID-OLD"),
        )
        conn.commit()

    second = client.post("/webhook/geeknews", json=payload, headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert second.json() == {"status": "matched", "openclaw_status": 200}

    # 25시간 전 매칭은 윈도우 밖 — OpenClaw 가 두 번 호출되어야 한다.
    assert route.call_count == 2


@respx.mock
def test_no_reply_first_call_does_not_dedupe_second_call(client: TestClient) -> None:
    """첫 호출이 no_reply 면 seen store 에 기록하지 않으므로, 두 번째 호출도 OpenClaw 를 친다."""
    route = respx.post(OPENCLAW_URL).mock(
        return_value=httpx.Response(200, json={"text": "NO_REPLY"}),
    )

    payload = {
        "title": "Sample",
        "url": "https://news.hada.io/topic?id=42",
        "guid": "GN-GUID-NOREPLY",
    }

    first = client.post("/webhook/geeknews", json=payload, headers=AUTH_HEADERS)
    assert first.status_code == 200
    assert first.json() == {"status": "no_reply", "openclaw_status": 200}

    second = client.post("/webhook/geeknews", json=payload, headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert second.json() == {"status": "no_reply", "openclaw_status": 200}

    # no_reply 는 dedupe 기록을 남기지 않으므로 OpenClaw 가 두 번 호출되어야 한다.
    assert route.call_count == 2
