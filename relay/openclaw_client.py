"""OpenClaw `/hooks/agent` 호출 래퍼.

plan.md §"Hook Invocation Pattern" 의 요청/응답 모양을 코드로 옮긴 얇은 비동기 클라이언트.
재시도 정책은 plan.md §"Robustness items in scope" 및 docs/convention.md §6 — transient 5xx 에
대해 1회 지수백오프 재시도, 그 외 transport error 는 즉시 OpenClawError 로 전환한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

MAX_RETRIES = 1
TIMEOUT_SECONDS = 10.0
# Exponential-backoff base per docs/convention.md §6 — sleep = BASE ** (attempt + 1).
BACKOFF_BASE_SECONDS = 2
NO_REPLY_TOKEN = "NO_REPLY"


class OpenClawError(Exception):
    """OpenClaw 호출이 최종 실패했을 때 raise — 라우트는 502 로 매핑."""


@dataclass
class OpenClawResponse:
    """OpenClaw 응답 파싱 결과 — 라우트는 이 값만 보고 응답을 결정한다."""

    status: Literal["matched", "no_reply"]
    http_status: int
    # no_reply 인 경우 OpenClaw 가 Slack 전송을 시도하지 않으므로 None.
    slack_delivered: bool | None


class OpenClawClient:
    """OpenClaw hook 호출 책임만 담당 — auth/validation/dedupe 는 호출부의 몫."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        # FastAPI lifespan 에서 만든 단일 AsyncClient 를 재사용한다 (docs/convention.md §7).
        self._client = http_client

    async def call_agent(
        self,
        *,
        title: str,
        url: str,
        excerpt: str | None,
    ) -> OpenClawResponse:
        """OpenClaw `/hooks/agent` 호출 — 5xx 1회 재시도, 응답을 OpenClawResponse 로 정규화."""
        # 환경변수는 호출 시점에 읽는다. 테스트가 monkeypatch.setenv 로 늦게 주입해도 동작하도록.
        openclaw_url = os.environ.get("OPENCLAW_URL", "")
        hook_token = os.environ.get("OPENCLAW_HOOK_TOKEN", "")
        agent_id = os.environ.get("OPENCLAW_AGENT_ID", "")
        slack_channel_id = os.environ.get("SLACK_CHANNEL_ID", "")

        body: dict[str, Any] = {
            "agentId": agent_id,
            "name": "GeekNews",
            "message": f"Title: {title}\nURL: {url}\nExcerpt: {excerpt or ''}",
            "deliver": "announce",
            "channel": "slack",
            "to": f"channel:{slack_channel_id}",
        }
        headers = {
            "Authorization": f"Bearer {hook_token}",
            "Content-Type": "application/json",
        }

        last_status: int | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._client.post(
                    openclaw_url,
                    json=body,
                    headers=headers,
                    timeout=TIMEOUT_SECONDS,
                )
            except httpx.TimeoutException as exc:
                # Transport 단계 실패는 재시도 대상이 아니다 (plan.md 가 5xx 만 명시).
                logger.error(
                    "openclaw_timeout",
                    extra={"url": openclaw_url, "error": exc.__class__.__name__},
                )
                raise OpenClawError("openclaw timeout") from exc
            except httpx.TransportError as exc:
                logger.error(
                    "openclaw_transport_error",
                    extra={"url": openclaw_url, "error": exc.__class__.__name__},
                )
                raise OpenClawError("openclaw transport error") from exc

            last_status = response.status_code
            if response.status_code >= 500:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "openclaw_retry",
                        extra={"attempt": attempt, "status": response.status_code},
                    )
                    await asyncio.sleep(BACKOFF_BASE_SECONDS ** (attempt + 1))
                    continue
                logger.error(
                    "openclaw_5xx_exhausted",
                    extra={"status": response.status_code},
                )
                raise OpenClawError(f"openclaw 5xx: {response.status_code}")

            if response.status_code >= 400:
                # 4xx 는 설정/요청 오류로 간주, 재시도하지 않는다.
                logger.error(
                    "openclaw_4xx",
                    extra={"status": response.status_code},
                )
                raise OpenClawError(f"openclaw 4xx: {response.status_code}")

            return _parse_success_response(response)

        # 루프는 위에서 반드시 return/raise 하므로 도달 불가 — 방어용.
        raise OpenClawError(f"openclaw unexpected fallthrough: {last_status}")


def _parse_success_response(response: httpx.Response) -> OpenClawResponse:
    """2xx 응답을 NO_REPLY 토큰 여부로 분기 — plan.md §"Agent Behavior Spec" 준수."""
    body_text: str = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        # `deliver_ok` 키 이름은 PR-04 스펙 노트의 가정값 — OpenClaw 실응답에 맞춰 향후 조정 가능.
        text_value = payload.get("text")
        if isinstance(text_value, str):
            body_text = text_value
    elif isinstance(payload, str):
        body_text = payload
    else:
        body_text = response.text

    if body_text.strip() == NO_REPLY_TOKEN:
        return OpenClawResponse(
            status="no_reply",
            http_status=response.status_code,
            slack_delivered=None,
        )

    slack_delivered: bool = True
    if isinstance(payload, dict):
        deliver_ok = payload.get("deliver_ok")
        if isinstance(deliver_ok, bool):
            slack_delivered = deliver_ok

    return OpenClawResponse(
        status="matched",
        http_status=response.status_code,
        slack_delivered=slack_delivered,
    )
