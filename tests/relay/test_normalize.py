"""relay.normalize 단위 테스트 — 정규화 규칙 회귀 방지."""

from __future__ import annotations

import pytest

from relay.normalize import dedupe_key, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # query string 만 다른 동일 글은 정규화 후 같은 키여야 한다.
        ("https://news.hada.io/topic?id=12345", "https://news.hada.io/topic"),
        # host/scheme 는 lowercase.
        ("HTTPS://NEWS.HADA.IO/topic?id=1", "https://news.hada.io/topic"),
        # trailing slash 제거 (root "/" 는 보존).
        ("https://example.com/post/1/", "https://example.com/post/1"),
        ("https://example.com/", "https://example.com/"),
        # fragment 도 항상 제거.
        ("https://example.com/post/1#section", "https://example.com/post/1"),
        # query + fragment 동시 제거.
        ("https://example.com/post/1?x=1&y=2#frag", "https://example.com/post/1"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_dedupe_key_prefers_guid() -> None:
    assert dedupe_key(guid="GUID-123", url="https://example.com/post/1?utm=a") == "GUID-123"


def test_dedupe_key_falls_back_to_normalized_url() -> None:
    assert (
        dedupe_key(guid=None, url="HTTPS://EXAMPLE.COM/post/1?utm=a")
        == "https://example.com/post/1"
    )
