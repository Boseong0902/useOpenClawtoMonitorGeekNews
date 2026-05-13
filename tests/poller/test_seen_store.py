"""Unit tests for poller.seen_store — empty DB / mark / repeated insert."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from poller.seen_store import init_db, is_seen, mark_seen


def test_is_seen_returns_false_on_empty_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "seen.sqlite")
    try:
        assert is_seen(conn, "nonexistent-key") is False
    finally:
        conn.close()


def test_mark_seen_then_is_seen_returns_true(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "seen.sqlite")
    try:
        now = datetime.now(UTC)
        mark_seen(conn, "k1", "title-1", now, relay_status=200, matched=True)
        assert is_seen(conn, "k1") is True
    finally:
        conn.close()


def test_mark_seen_idempotent_on_duplicate_key(tmp_path: Path) -> None:
    # 같은 key 로 두 번 mark 해도 PRIMARY KEY conflict 가 raise 되지 않아야 한다.
    conn = init_db(tmp_path / "seen.sqlite")
    try:
        now = datetime.now(UTC)
        mark_seen(conn, "k1", "title-1", now, relay_status=200, matched=True)
        mark_seen(conn, "k1", "title-1-updated", now, relay_status=200, matched=False)
        assert is_seen(conn, "k1") is True
        # 최신 row 의 matched 값이 반영되었는지 확인 — INSERT OR REPLACE 의미.
        row = conn.execute("SELECT title, matched FROM seen WHERE key = ?", ("k1",)).fetchone()
        assert row == ("title-1-updated", 0)
    finally:
        conn.close()


def test_is_seen_distinguishes_keys(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "seen.sqlite")
    try:
        now = datetime.now(UTC)
        mark_seen(conn, "k1", "t", now, relay_status=200, matched=True)
        assert is_seen(conn, "k1") is True
        assert is_seen(conn, "k2") is False
    finally:
        conn.close()
