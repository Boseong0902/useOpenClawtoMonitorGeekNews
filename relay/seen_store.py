"""Relay-side seen store — 24h dedupe re-check 백엔드.

plan.md §"SeenStore Schema" 의 스키마를 그대로 재사용한다 (poller seen.sqlite 와 동일 컬럼).
함수는 모두 sync — async 라우트에서 `asyncio.to_thread` 로 호출해 이벤트 루프를 막지 않는다.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

_DEDUPE_WINDOW = timedelta(hours=24)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    title TEXT,
    seen_at TEXT NOT NULL,
    relay_status INTEGER,
    matched INTEGER
)
"""

_CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_seen_at ON seen(seen_at)"


def init_db(path: str | Path) -> sqlite3.Connection:
    """Open the seen-store SQLite file and ensure schema is in place.

    Accepts str | Path so callers can pass either env-loaded strings or
    pathlib paths. The returned connection is owned by the caller (close
    on lifespan shutdown).
    """
    # 테스트 tmp_path 는 존재하지만, 운영 경로는 부모 디렉터리가 없을 수 있으므로 보장한다.
    db_path = Path(path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False — async 라우트가 asyncio.to_thread 로 호출하면
    # 워커 스레드가 매번 달라지므로 thread 체크를 끄고, 직렬화는 호출부 (단일 라우트)에 맡긴다.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(_CREATE_TABLE_SQL)
    conn.execute(_CREATE_INDEX_SQL)
    conn.commit()
    return conn


def has_recent_match(conn: sqlite3.Connection, key: str, now: datetime) -> bool:
    """Return True iff `key` produced a `matched` result within the last 24h.

    ISO 8601 UTC 문자열은 사전식 정렬이 시간 정렬과 일치하므로
    seen_at >= (now - 24h).isoformat() 로 비교한다.
    """
    threshold = (now - _DEDUPE_WINDOW).isoformat()
    cursor = conn.execute(
        "SELECT 1 FROM seen WHERE key = ? AND matched = 1 AND seen_at >= ? LIMIT 1",
        (key, threshold),
    )
    row = cursor.fetchone()
    return row is not None


def record_match(
    conn: sqlite3.Connection,
    key: str,
    title: str,
    now: datetime,
    openclaw_status: int,
) -> None:
    """Upsert a `matched` row for `key` with `seen_at=now` and the relay status.

    INSERT OR REPLACE 로 같은 key 가 다시 매칭되면 seen_at 을 최신으로 밀어
    24h 윈도우가 마지막 매칭 시점부터 다시 시작되도록 한다.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO seen (key, title, seen_at, relay_status, matched)
        VALUES (?, ?, ?, ?, 1)
        """,
        (key, title, now.isoformat(), openclaw_status),
    )
    conn.commit()
