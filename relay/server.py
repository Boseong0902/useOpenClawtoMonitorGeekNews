from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from relay.logging_setup import configure_logging, log_relay_decision
from relay.models import WebhookPayload
from relay.openclaw_client import OpenClawClient, OpenClawError

load_dotenv()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    async with httpx.AsyncClient() as client:
        # 단위 테스트가 raw httpx 를 갈아끼울 수 있도록 bare client 도 그대로 노출한다.
        app.state.openclaw_client = client
        app.state.openclaw_client_wrapper = OpenClawClient(client)
        yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    # plan.md 의 응답 스키마는 FastAPI 기본 422가 아니라 400 + {"error": <field>}.
    field = "validation"
    for err in exc.errors():
        if err.get("type") == "missing":
            loc = err.get("loc", ())
            if loc:
                field = str(loc[-1])
                break
    return JSONResponse(status_code=400, content={"error": field})


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # detail 이 dict 면 그대로 body 로 보낸다 — plan.md 의 {"error": "auth"} 스키마 일치.
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def verify_secret(x_webhook_secret: str = Header(default="")) -> None:
    """X-Webhook-Secret 헤더를 상수시간 비교. 미스매치/누락 → 401."""
    expected = os.environ.get("RELAY_SHARED_SECRET", "")
    if not expected or not hmac.compare_digest(x_webhook_secret, expected):
        raise HTTPException(status_code=401, detail={"error": "auth"})


@app.post("/webhook/geeknews")
async def webhook_geeknews(
    request: Request,
    payload: WebhookPayload,
    _: None = Depends(verify_secret),
) -> JSONResponse:
    guid_or_url = payload.guid or payload.url
    # request.app.state 경유 — 모듈-레벨 app 캡처 대신 런타임 인스턴스를 참조한다.
    wrapper: OpenClawClient = request.app.state.openclaw_client_wrapper

    try:
        result = await wrapper.call_agent(
            title=payload.title,
            url=payload.url,
            excerpt=payload.excerpt,
        )
    except OpenClawError:
        log_relay_decision(
            logger=logger,
            guid_or_url=guid_or_url,
            dedupe_decision="pass",
            openclaw_status=None,
            slack_delivered=None,
        )
        return JSONResponse(status_code=502, content={"error": "openclaw"})

    if result.status == "no_reply":
        log_relay_decision(
            logger=logger,
            guid_or_url=guid_or_url,
            dedupe_decision="pass",
            openclaw_status=result.http_status,
            slack_delivered=None,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "no_reply", "openclaw_status": result.http_status},
        )

    # status == "matched"
    if result.slack_delivered is False:
        # OpenClaw 는 받았지만 Slack 전송이 실패 — 503 으로 분리해 로그 grep 가능하게 한다.
        log_relay_decision(
            logger=logger,
            guid_or_url=guid_or_url,
            dedupe_decision="pass",
            openclaw_status=result.http_status,
            slack_delivered=False,
        )
        return JSONResponse(status_code=503, content={"error": "slack"})

    log_relay_decision(
        logger=logger,
        guid_or_url=guid_or_url,
        dedupe_decision="pass",
        openclaw_status=result.http_status,
        slack_delivered=True,
    )
    return JSONResponse(
        status_code=200,
        content={"status": "matched", "openclaw_status": result.http_status},
    )
