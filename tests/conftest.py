"""Shared pytest fixtures for the geeknews relay/poller suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """relay.server.app 을 띄운 TestClient — RELAY_SHARED_SECRET=test, LOG_FILE=tmp 로 격리."""
    monkeypatch.setenv("RELAY_SHARED_SECRET", "test")
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "relay.log"))

    # Env 가 설정된 뒤에 import 해야 configure_logging() 이 tmp LOG_FILE 을 잡는다.
    from relay.server import app

    with TestClient(app) as test_client:
        yield test_client
