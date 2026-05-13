from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from relay.logging_setup import configure_logging, log_relay_decision
from relay.models import WebhookPayload
from relay.normalize import dedupe_key
from relay.openclaw_client import OpenClawClient, OpenClawError
from relay.seen_store import has_recent_match, init_db, record_match

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_SEEN_DB_PATH = "relay/relay_seen.sqlite"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    seen_db_path = os.environ.get("RELAY_SEEN_DB_PATH", _DEFAULT_SEEN_DB_PATH)
    seen_conn: sqlite3.Connection = init_db(seen_db_path)
    logger.info("seen_store_opened", extra={"path": str(seen_db_path)})
    app.state.seen_conn = seen_conn
    try:
        async with httpx.AsyncClient() as client:
            # 단위 테스트가 raw httpx 를 갈아끼울 수 있도록 bare client 도 그대로 노출한다.
            app.state.openclaw_client = client
            app.state.openclaw_client_wrapper = OpenClawClient(client)
            yield
    finally:
        seen_conn.close()


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
    key = dedupe_key(guid=payload.guid, url=payload.url)
    now = datetime.now(UTC)
    seen_conn: sqlite3.Connection = request.app.state.seen_conn

    # 24h 안에 같은 key 가 matched 였으면 OpenClaw 호출 없이 short-circuit.
    # NOTE: has_recent_match → record_match 사이의 race window 는 본 과제 트래픽
    # (poller 가 5분에 수 건 수준) 에서 허용한다. 동시 동일 key 요청이 모두 "no match"
    # 를 보고 OpenClaw 까지 도달할 수는 있지만, 다음 사이클에서 dedupe 되므로 무해하다.
    if await asyncio.to_thread(has_recent_match, seen_conn, key, now):
        log_relay_decision(
            logger=logger,
            guid_or_url=key,
            dedupe_decision="skip",
            openclaw_status=None,
            slack_delivered=None,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "no_reply", "reason": "dedupe"},
        )

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
            guid_or_url=key,
            dedupe_decision="pass",
            openclaw_status=None,
            slack_delivered=None,
        )
        return JSONResponse(status_code=502, content={"error": "openclaw"})

    if result.status == "no_reply":
        log_relay_decision(
            logger=logger,
            guid_or_url=key,
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
            guid_or_url=key,
            dedupe_decision="pass",
            openclaw_status=result.http_status,
            slack_delivered=False,
        )
        return JSONResponse(status_code=503, content={"error": "slack"})

    # matched + slack 전송 성공 — 24h 윈도우의 기준이 되는 시점이므로 seen store 에 기록.
    await asyncio.to_thread(
        record_match,
        seen_conn,
        key,
        payload.title,
        now,
        result.http_status,
    )

    log_relay_decision(
        logger=logger,
        guid_or_url=key,
        dedupe_decision="pass",
        openclaw_status=result.http_status,
        slack_delivered=True,
    )
    return JSONResponse(
        status_code=200,
        content={"status": "matched", "openclaw_status": result.http_status},
    )
