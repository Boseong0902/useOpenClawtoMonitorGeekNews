"""Poller-side seen store — cron tick 마다 RSS 신규 항목을 식별하기 위한 dedupe.

plan.md §"SeenStore Schema" 의 스키마를 그대로 따른다. relay-side seen store 와
컬럼/인덱스는 동일하지만, "일단 복사, 추후 공용화는 over-engineering" 원칙으로 별도
모듈을 둔다 — cron job 이 relay/ 디렉터리를 import 하지 않아도 standalone 으로 동작.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

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
    """Open the poller seen-store SQLite file and ensure schema is in place.

    poller 는 cron one-shot 이라 단일 스레드 sync 동작 — check_same_thread 기본값으로
    충분. 운영 경로에 부모 디렉터리가 없을 수 있으므로 생성 보장.
    """
    db_path = Path(path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE_TABLE_SQL)
    conn.execute(_CREATE_INDEX_SQL)
    conn.commit()
    return conn


def is_seen(conn: sqlite3.Connection, key: str) -> bool:
    """Return True iff a row with the given dedupe key already exists.

    poller 는 "이미 한 번이라도 본 글이면 다시 relay 로 쏘지 않는다" 가 정책이므로
    matched 여부에 관계없이 key 존재만으로 short-circuit.
    """
    cursor = conn.execute(
        "SELECT 1 FROM seen WHERE key = ? LIMIT 1",
        (key,),
    )
    return cursor.fetchone() is not None


def mark_seen(
    conn: sqlite3.Connection,
    key: str,
    title: str,
    now: datetime,
    relay_status: int,
    matched: bool,
) -> None:
    """Upsert a row for `key` recording the relay outcome at `now`.

    INSERT OR REPLACE 로 같은 key 가 다시 들어와도 PRIMARY KEY conflict 없이
    최신 결과로 덮어쓴다 — 같은 글이 다시 보이는 일은 정책상 없지만 방어적으로.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO seen (key, title, seen_at, relay_status, matched)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key, title, now.isoformat(), relay_status, 1 if matched else 0),
    )
    conn.commit()
