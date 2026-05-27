"""Poller — GeekNews RSS 폴링 + dedupe + relay POST.

cron one-shot 으로 5분마다 실행되며, RSS 의 신규 항목만 relay 에 forwarding 한다.
relay 의 HTTP 계약 (plan.md §"Relay HTTP Specification") 만 신뢰하면 되므로
relay 모듈을 import 하지 않는다 — 정규화 로직은 일부러 복사한다 (PR-06 §"Why").
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests
from dotenv import load_dotenv
from pythonjsonlogger import jsonlogger

from poller.seen_store import init_db, is_seen, mark_seen

logger = logging.getLogger(__name__)

_EXCERPT_SLICE_LEN = 250
_RELAY_POST_TIMEOUT_S = 10.0
_DEFAULT_SEEN_DB_PATH = "poller/seen.sqlite"
_CONFIGURED_FLAG = "_poller_json_logging_configured"
_MAX_FEED_ENTRIES = 70


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


def configure_logging() -> logging.Logger:
    """Install JSON formatter + console/file handlers on the root logger (idempotent).

    docs/convention.md §5 의 5필드 (timestamp, guid_or_url, dedupe_decision,
    openclaw_status, slack_delivered) 를 relay 로그와 같은 구조로 남기기 위함 —
    화면 캡처 #4 의 end-to-end log trace 가 동일 grep 으로 연결되도록.
    """
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return root
    root.setLevel(logging.INFO)
    formatter = jsonlogger.JsonFormatter()  # type: ignore[attr-defined]

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    log_file = os.environ.get("LOG_FILE", "poller.log")
    try:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning(
            "log_file_open_failed",
            extra={"log_file": log_file, "error": str(exc)},
        )

    setattr(root, _CONFIGURED_FLAG, True)
    return root


def _log_poller_decision(
    *,
    guid_or_url: str,
    dedupe_decision: Literal["pass", "skip"],
    openclaw_status: int | None,
) -> None:
    """relay 와 동일한 5필드 schema 로 한 줄 JSON 로그.

    `slack_delivered` 는 poller 가 직접 알 수 없으므로 항상 None — relay 로그에서
    동일 guid_or_url 로 grep 해서 합치는 흐름 (스크린샷 #4).
    """
    logger.info(
        "poller_decision",
        extra={
            "timestamp": datetime.now(UTC).isoformat(),
            "guid_or_url": guid_or_url,
            "dedupe_decision": dedupe_decision,
            "openclaw_status": openclaw_status,
            "slack_delivered": None,
        },
    )


@dataclass(frozen=True)
class OpenClawResult:
    """openclaw agent CLI 호출 결과."""

    status: Literal["matched", "no_reply", "error"]
    text: str | None


_OPENCLAW_TIMEOUT_S = 120
_NO_REPLY_TOKEN = "NO_REPLY"


def send_to_slack(text: str, *, channel_id: str, bot_token: str) -> bool:
    """Slack API로 메시지를 1회만 전송. 성공 시 True."""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    client = WebClient(token=bot_token)
    try:
        client.chat_postMessage(channel=channel_id, text=text)
    except SlackApiError as exc:
        logger.error(
            "slack_send_failed",
            extra={"channel": channel_id, "error": str(exc.response["error"])},
        )
        return False
    return True


def call_openclaw_agent(
    entry: FeedEntry,
    *,
    agent_id: str,
    slack_channel_id: str,
) -> OpenClawResult:
    """openclaw agent CLI로 에이전트 판단만 수행 (Slack 전송은 caller가 직접)."""
    import uuid

    session_key = f"poller-{uuid.uuid4().hex[:12]}"
    message = f"Title: {entry.title}\nURL: {entry.url}\nExcerpt: {entry.excerpt or ''}"
    cmd = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--message",
        message,
        "--session-key",
        session_key,
        "--timeout",
        str(_OPENCLAW_TIMEOUT_S),
        "--json",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_OPENCLAW_TIMEOUT_S + 30,
        )
    except subprocess.TimeoutExpired:
        logger.error("openclaw_cli_timeout", extra={"entry_url": entry.url})
        return OpenClawResult(status="error", text=None)
    except OSError as exc:
        logger.error(
            "openclaw_cli_exec_failed",
            extra={"error": exc.__class__.__name__, "entry_url": entry.url},
        )
        return OpenClawResult(status="error", text=None)

    if result.returncode != 0:
        logger.error(
            "openclaw_cli_nonzero",
            extra={
                "returncode": result.returncode,
                "stderr": result.stderr[:500] if result.stderr else "",
                "entry_url": entry.url,
            },
        )
        return OpenClawResult(status="error", text=None)

    try:
        payload = json.loads(result.stdout)
        # gateway: {"result": {"payloads": [...]}}  /  embedded: {"payloads": [...]}
        inner = payload.get("result", payload)
        payloads: list[dict[str, Any]] = inner.get("payloads", [])
    except (json.JSONDecodeError, AttributeError):
        logger.error(
            "openclaw_cli_parse_failed",
            extra={"stdout": result.stdout[:500], "entry_url": entry.url},
        )
        return OpenClawResult(status="error", text=None)

    if not payloads:
        return OpenClawResult(status="no_reply", text=None)

    text = payloads[0].get("text", "")
    if not text or text.strip() == _NO_REPLY_TOKEN:
        return OpenClawResult(status="no_reply", text=None)

    return OpenClawResult(status="matched", text=text)


def _required_env(name: str) -> str | None:
    """Return env value or None (caller logs CRITICAL and exits)."""
    value = os.environ.get(name, "").strip()
    return value or None


def main() -> int:
    """Cron one-shot 엔트리포인트. 정상 흐름 0, 환경 누락 등 fatal 은 비-0 반환."""
    load_dotenv()
    configure_logging()

    rss_feed_url = _required_env("RSS_FEED_URL")
    slack_channel_id = _required_env("SLACK_CHANNEL_ID")
    slack_bot_token = _required_env("SLACK_BOT_TOKEN")
    agent_id = os.environ.get("OPENCLAW_AGENT_ID", "gn-monitor")
    if not rss_feed_url or not slack_channel_id or not slack_bot_token:
        logger.critical(
            "poller_missing_env",
            extra={
                "rss_feed_url_set": bool(rss_feed_url),
                "slack_channel_id_set": bool(slack_channel_id),
                "slack_bot_token_set": bool(slack_bot_token),
            },
        )
        return 2

    seen_db_path = os.environ.get("POLLER_SEEN_DB_PATH", _DEFAULT_SEEN_DB_PATH)

    try:
        conn = init_db(seen_db_path)
    except sqlite3.Error as exc:
        logger.critical(
            "poller_seen_db_open_failed",
            extra={"path": seen_db_path, "error": exc.__class__.__name__},
        )
        return 3

    try:
        entries = fetch_feed(rss_feed_url)[:_MAX_FEED_ENTRIES]
        logger.info("poller_fetched", extra={"count": len(entries), "url": rss_feed_url})

        for entry in entries:
            key = dedupe_key(entry)
            try:
                if is_seen(conn, key):
                    _log_poller_decision(
                        guid_or_url=key,
                        dedupe_decision="skip",
                        openclaw_status=None,
                    )
                    continue
            except sqlite3.Error as exc:
                logger.error(
                    "poller_seen_check_failed",
                    extra={"guid_or_url": key, "error": exc.__class__.__name__},
                )
                continue

            result = call_openclaw_agent(
                entry,
                agent_id=agent_id,
                slack_channel_id=slack_channel_id,
            )
            if result.status == "error":
                _log_poller_decision(
                    guid_or_url=key,
                    dedupe_decision="pass",
                    openclaw_status=None,
                )
                continue

            slack_delivered: bool | None = None
            if result.status == "matched" and result.text:
                slack_delivered = send_to_slack(
                    result.text,
                    channel_id=slack_channel_id,
                    bot_token=slack_bot_token,
                )

            now = datetime.now(UTC)
            try:
                mark_seen(
                    conn,
                    key,
                    entry.title,
                    now,
                    relay_status=0,
                    matched=result.status == "matched",
                )
            except sqlite3.Error as exc:
                logger.error(
                    "poller_seen_mark_failed",
                    extra={"guid_or_url": key, "error": exc.__class__.__name__},
                )
            _log_poller_decision(
                guid_or_url=key,
                dedupe_decision="pass",
                openclaw_status=200 if result.status == "matched" else None,
            )
            if slack_delivered is not None:
                logger.info(
                    "slack_delivered",
                    extra={"guid_or_url": key, "delivered": slack_delivered},
                )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
