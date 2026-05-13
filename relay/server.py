from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from relay.logging_setup import configure_logging

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    async with httpx.AsyncClient() as client:
        app.state.openclaw_client = client
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
