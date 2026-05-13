"""Unit tests for poller.poll_geeknews — feed parsing + dedupe_key + _normalize_url."""

from __future__ import annotations

from poller.poll_geeknews import (
    FeedEntry,
    _normalize_url,
    dedupe_key,
    fetch_feed,
)

# 고정 RSS XML 문자열로 회귀 테스트 — 업스트림 shape 이 바뀌면 여기서 잡힌다.
_FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>GeekNews</title>
    <link>https://news.hada.io/</link>
    <description>tech news</description>
    <item>
      <title>Item With GUID</title>
      <link>https://news.hada.io/topic?id=100</link>
      <guid>https://news.hada.io/topic?id=100</guid>
      <description>This is the first item summary.</description>
      <pubDate>Wed, 13 May 2026 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Item Without GUID</title>
      <link>https://news.hada.io/Topic?id=200&amp;ref=rss</link>
      <description>Second item summary text body.</description>
    </item>
  </channel>
</rss>
"""


def test_fetch_feed_parses_two_entries() -> None:
    entries = fetch_feed(_FIXTURE_XML)
    assert len(entries) == 2

    first, second = entries
    assert first.title == "Item With GUID"
    assert first.url == "https://news.hada.io/topic?id=100"
    assert first.guid == "https://news.hada.io/topic?id=100"
    assert "first item summary" in first.excerpt

    assert second.title == "Item Without GUID"
    assert second.url == "https://news.hada.io/Topic?id=200&ref=rss"
    # GUID 없는 항목은 None 으로 들어와야 한다 — fallback 로직이 동작하도록.
    assert second.guid is None


def test_dedupe_key_prefers_guid_then_falls_back_to_normalized_url() -> None:
    guid_entry = FeedEntry(
        title="t",
        url="https://news.hada.io/topic?id=1",
        guid="abc-123",
        excerpt="",
        published_at=None,
    )
    no_guid_entry = FeedEntry(
        title="t",
        url="HTTPS://News.Hada.IO/topic/?utm=x#frag",
        guid=None,
        excerpt="",
        published_at=None,
    )

    assert dedupe_key(guid_entry) == "abc-123"
    # query, fragment 제거 + scheme/host lowercase + trailing slash 제거.
    assert dedupe_key(no_guid_entry) == "https://news.hada.io/topic"


def test_normalize_url_handles_scheme_host_query_fragment_trailing_slash() -> None:
    assert _normalize_url("HTTPS://Example.COM/Path?q=1&a=2#frag") == "https://example.com/Path"
    assert _normalize_url("http://example.com/path/") == "http://example.com/path"
    # 루트 경로는 그대로 둔다 — host-only 표현과 충돌 방지.
    assert _normalize_url("http://example.com/") == "http://example.com/"
    assert _normalize_url("http://EXAMPLE.com/abc") == "http://example.com/abc"


def test_fetch_feed_returns_empty_on_malformed_feed() -> None:
    # XML 자체가 아예 깨진 경우 → entries 가 비어있어 빈 리스트 반환.
    entries = fetch_feed("<<<not xml at all>>>")
    assert entries == []


def test_fetch_feed_skips_entries_missing_title_or_link() -> None:
    xml = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
  <item><title>no link</title></item>
  <item><link>https://x.com/p</link></item>
  <item><title>complete</title><link>https://x.com/ok</link></item>
</channel></rss>"""
    entries = fetch_feed(xml)
    assert len(entries) == 1
    assert entries[0].title == "complete"
