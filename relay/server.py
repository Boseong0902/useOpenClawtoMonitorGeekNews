from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI

from relay.logging_setup import configure_logging

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    async with httpx.AsyncClient() as client:
        app.state.openclaw_client = client
        yield


app = FastAPI(lifespan=lifespan)
