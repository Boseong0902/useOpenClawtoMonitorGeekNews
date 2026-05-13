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
    # 테스트 간 dedupe 상태가 공유되지 않게 tmp_path 안에 격리된 SQLite 파일을 쓴다.
    monkeypatch.setenv("RELAY_SEEN_DB_PATH", str(tmp_path / "relay_seen.sqlite"))
    # OpenClaw 호출 시 사용할 환경변수 — respx 가 가로채므로 실제 호스트는 의미 없음.
    monkeypatch.setenv("OPENCLAW_URL", "http://127.0.0.1:18789/hooks/agent")
    monkeypatch.setenv("OPENCLAW_HOOK_TOKEN", "test-hook-token")
    monkeypatch.setenv("OPENCLAW_AGENT_ID", "gn-monitor")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")

    # Env 가 설정된 뒤에 import 해야 configure_logging() 이 tmp LOG_FILE 을 잡는다.
    from relay.server import app

    with TestClient(app) as test_client:
        yield test_client
