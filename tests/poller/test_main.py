"""End-to-end tests for poller.poll_geeknews.main() — retry contract + env validation.

핵심 invariant: relay 가 실패하면 (None 반환) `mark_seen` 이 호출되지 않아 다음 cron tick
이 같은 항목을 재시도한다 (poller/poll_geeknews.py:347-355 의 early `continue`).
이 계약은 코드 인스펙션만으로는 보장되지 않으므로 SQLite 의 실제 상태로 검증한다 —
미래의 리팩터가 `mark_seen` 을 가드 밖으로 옮기는 silent drop 회귀를 막기 위함.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests_mock as rm_module

from poller.poll_geeknews import FeedEntry, dedupe_key, main
from poller.seen_store import init_db, is_seen, mark_seen

_RELAY_URL = "http://127.0.0.1:8080/webhook/geeknews"
_SECRET = "test-secret"
_RSS_URL = "https://news.hada.io/rss"


def _make_entry() -> FeedEntry:
    return FeedEntry(
        title="Sample Title",
        url="https://news.hada.io/topic?id=42",
        guid="g-42",
        excerpt="hello",
        published_at=None,
    )


@pytest.fixture
def main_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """main() 이 필요로 하는 env 와 stub 들을 격리해서 세팅."""
    db_path = tmp_path / "seen.sqlite"
    monkeypatch.setenv("RSS_FEED_URL", _RSS_URL)
    monkeypatch.setenv("RELAY_URL", _RELAY_URL)
    monkeypatch.setenv("RELAY_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("POLLER_SEEN_DB_PATH", str(db_path))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "poller.log"))
    # 실제 .env 가 테스트 env 를 덮어쓰지 않도록 load_dotenv 를 무력화.
    monkeypatch.setattr("poller.poll_geeknews.load_dotenv", lambda: None)
    # fetch_feed 는 네트워크 없이 고정된 한 개 엔트리를 반환.
    monkeypatch.setattr(
        "poller.poll_geeknews.fetch_feed",
        lambda url: [_make_entry()],
    )
    yield db_path


def test_main_skips_mark_seen_on_relay_failure(
    main_env: Path, requests_mock: rm_module.Mocker
) -> None:
    """relay 5xx 면 seen 에 기록되지 않아야 한다 — 다음 tick 재시도 보장."""
    requests_mock.post(_RELAY_URL, status_code=502, json={"error": "openclaw"})

    rc = main()
    assert rc == 0  # cron-friendly: 개별 항목 실패는 프로세스 종료 코드와 무관.

    # main() 의 conn 은 닫혔으므로 새로 열어서 상태 검증.
    conn = sqlite3.connect(str(main_env))
    try:
        assert is_seen(conn, dedupe_key(_make_entry())) is False
    finally:
        conn.close()


def test_main_marks_seen_on_relay_success_matched(
    main_env: Path, requests_mock: rm_module.Mocker
) -> None:
    """relay 가 matched 200 응답 → seen 에 기록되어야 한다."""
    requests_mock.post(
        _RELAY_URL,
        status_code=200,
        json={"status": "matched", "openclaw_status": 200},
    )

    rc = main()
    assert rc == 0

    conn = sqlite3.connect(str(main_env))
    try:
        assert is_seen(conn, dedupe_key(_make_entry())) is True
    finally:
        conn.close()


def test_main_marks_seen_on_relay_success_no_reply(
    main_env: Path, requests_mock: rm_module.Mocker
) -> None:
    """LLM 이 no_reply 로 필터한 경우에도 seen 기록 — 다음 tick 에 재질의 방지."""
    requests_mock.post(
        _RELAY_URL,
        status_code=200,
        json={"status": "no_reply", "openclaw_status": 200},
    )

    rc = main()
    assert rc == 0

    conn = sqlite3.connect(str(main_env))
    try:
        assert is_seen(conn, dedupe_key(_make_entry())) is True
    finally:
        conn.close()


def test_main_skips_already_seen_entry(main_env: Path, requests_mock: rm_module.Mocker) -> None:
    """이미 seen 에 있는 항목은 relay 호출 자체가 발생하지 않아야 한다."""
    entry = _make_entry()
    key = dedupe_key(entry)

    # main() 이전에 seen DB 를 미리 채워둔다.
    conn = init_db(main_env)
    try:
        from datetime import UTC, datetime

        mark_seen(
            conn,
            key,
            entry.title,
            datetime.now(UTC),
            relay_status=200,
            matched=True,
        )
    finally:
        conn.close()

    # relay 모킹은 등록만 해 두고 호출되지 않음을 검증.
    requests_mock.post(
        _RELAY_URL,
        status_code=200,
        json={"status": "matched", "openclaw_status": 200},
    )

    rc = main()
    assert rc == 0
    assert requests_mock.call_count == 0


def test_main_returns_nonzero_on_missing_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RSS_FEED_URL 누락 → fatal, 비-0 반환 (cron 이 실패를 감지하도록)."""
    # main_env 픽스처를 쓰지 않고 직접 세팅 — RSS_FEED_URL 만 빠진 상태로.
    monkeypatch.delenv("RSS_FEED_URL", raising=False)
    monkeypatch.setenv("RELAY_URL", _RELAY_URL)
    monkeypatch.setenv("RELAY_SHARED_SECRET", _SECRET)
    db_path = tmp_path / "seen.sqlite"
    monkeypatch.setenv("POLLER_SEEN_DB_PATH", str(db_path))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "poller.log"))
    monkeypatch.setattr("poller.poll_geeknews.load_dotenv", lambda: None)

    rc = main()
    assert rc != 0
    # DB 도 열리기 전에 종료되어야 한다 — 파일 생성되지 않음.
    assert not db_path.exists()
