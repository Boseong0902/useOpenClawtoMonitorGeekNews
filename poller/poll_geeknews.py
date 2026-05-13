"""Poller — GeekNews RSS 폴링 + dedupe + relay POST.

cron one-shot 으로 5분마다 실행되며, RSS 의 신규 항목만 relay 에 forwarding 한다.
relay 의 HTTP 계약 (plan.md §"Relay HTTP Specification") 만 신뢰하면 되므로
relay 모듈을 import 하지 않는다 — 정규화 로직은 일부러 복사한다 (PR-06 §"Why").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

logger = logging.getLogger(__name__)

_EXCERPT_SLICE_LEN = 250
_RELAY_POST_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class FeedEntry:
    """RSS entry 의 최소 representation — relay POST 페이로드의 source-of-truth."""

    title: str
    url: str
    guid: str | None
    excerpt: str
    published_at: str | None


def fetch_feed(url: str) -> list[FeedEntry]:
    """Parse RSS at `url` and return entries as `FeedEntry` objects.

    feedparser 는 string URL 과 raw XML string 둘 다 받는다 — 테스트에서는 후자.
    bozo=True 인 경우 (피드 malformed) 는 로그를 남기고 빈 리스트를 반환해서
    cron tick 이 조용히 통과하도록 한다 — 다음 tick 에서 다시 시도.
    """
    parsed: Any = feedparser.parse(url)
    if getattr(parsed, "bozo", False):
        bozo_exc = getattr(parsed, "bozo_exception", None)
        logger.warning(
            "feed_parse_bozo",
            extra={"url": url, "error": str(bozo_exc) if bozo_exc else "unknown"},
        )
        # malformed 라도 entries 가 부분적으로 있으면 처리한다 — feedparser 관용 모드.
        if not getattr(parsed, "entries", None):
            return []

    entries: list[FeedEntry] = []
    for raw_entry in parsed.entries:
        title = _safe_str(raw_entry.get("title"))
        link = _safe_str(raw_entry.get("link"))
        if not title or not link:
            # title/url 은 relay 페이로드 필수 필드 — 빠지면 그냥 스킵.
            continue
        guid_value = raw_entry.get("id") or raw_entry.get("guid")
        guid: str | None = guid_value if isinstance(guid_value, str) and guid_value else None
        summary_raw = _safe_str(raw_entry.get("summary"))
        excerpt = summary_raw[:_EXCERPT_SLICE_LEN]
        published_at_value = raw_entry.get("published") or raw_entry.get("updated")
        published_at: str | None = (
            published_at_value
            if isinstance(published_at_value, str) and published_at_value
            else None
        )
        entries.append(
            FeedEntry(
                title=title,
                url=link,
                guid=guid,
                excerpt=excerpt,
                published_at=published_at,
            )
        )
    return entries


def _safe_str(value: Any) -> str:
    """feedparser 가 종종 비-string 값을 반환하므로 안전하게 string 화."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop query+fragment, strip trailing slash.

    relay/normalize.py 의 `normalize_url` 와 동일 로직을 복사한 사본이다 —
    PR-06 spec §"Why" 가 명시: "일단 복사, 추후 공용화는 over-engineering".
    cron job 이 relay/ 를 import 하지 않아도 standalone 으로 돌아가도록 유지.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def dedupe_key(entry: FeedEntry) -> str:
    """RSS `guid` 우선, 없으면 정규화 URL — plan.md §"Trigger Source Decision > Dedupe key"."""
    if entry.guid:
        return entry.guid
    return _normalize_url(entry.url)


@dataclass(frozen=True)
class RelayResponse:
    """Relay 응답을 caller 가 다루기 쉽도록 정규화한 형태."""

    status: Literal["matched", "no_reply"]
    openclaw_status: int | None
    http_status: int


def post_to_relay(
    entry: FeedEntry,
    *,
    relay_url: str,
    secret: str,
) -> RelayResponse | None:
    """Forward `entry` to the relay; return `None` on any failure.

    실패 시 None 을 반환하면 caller 는 seen 에 기록하지 않아야 한다 — 다음 cron tick
    이 재시도할 기회를 보존해야 transient 네트워크 이슈에 강함 (PR-06 spec commit 3 §"Why").
    `requests.RequestException` (transport), `ValueError` (JSON), `KeyError`
    (missing fields) 만 narrow catch. 그 외 예외는 caller 까지 전파한다.
    """
    body: dict[str, Any] = {
        "title": entry.title,
        "url": entry.url,
        "guid": entry.guid,
        "excerpt": entry.excerpt,
        "published_at": entry.published_at,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": secret,
    }
    try:
        response = requests.post(
            relay_url,
            json=body,
            headers=headers,
            timeout=_RELAY_POST_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.error(
            "relay_post_transport_error",
            extra={
                "url": relay_url,
                "guid_or_url": entry.guid or entry.url,
                "error": exc.__class__.__name__,
            },
        )
        return None

    if response.status_code < 200 or response.status_code >= 300:
        # relay 의 4xx/5xx 는 dedupe 미기록으로 — 다음 tick 에서 재시도되도록.
        logger.error(
            "relay_post_non_2xx",
            extra={
                "url": relay_url,
                "guid_or_url": entry.guid or entry.url,
                "relay_status": response.status_code,
            },
        )
        return None

    try:
        payload = response.json()
        status_value = payload["status"]
    except ValueError:
        logger.error(
            "relay_post_invalid_json",
            extra={
                "url": relay_url,
                "guid_or_url": entry.guid or entry.url,
                "relay_status": response.status_code,
            },
        )
        return None
    except (KeyError, TypeError):
        logger.error(
            "relay_post_missing_status",
            extra={
                "url": relay_url,
                "guid_or_url": entry.guid or entry.url,
                "relay_status": response.status_code,
            },
        )
        return None

    if status_value not in ("matched", "no_reply"):
        logger.error(
            "relay_post_unknown_status",
            extra={
                "url": relay_url,
                "guid_or_url": entry.guid or entry.url,
                "relay_status": response.status_code,
                "body_status": status_value,
            },
        )
        return None

    openclaw_status_raw = payload.get("openclaw_status") if isinstance(payload, dict) else None
    openclaw_status: int | None = (
        openclaw_status_raw if isinstance(openclaw_status_raw, int) else None
    )

    return RelayResponse(
        status=status_value,
        openclaw_status=openclaw_status,
        http_status=response.status_code,
    )
