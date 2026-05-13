"""URL 정규화 + dedupe key 계산.

plan.md §"Trigger Source Decision > Dedupe key" 의 규칙을 한 곳에 모아둔다 —
`?utm_source=...` 같은 트래킹 쿼리가 붙은 동일 글이 두 번 매칭되지 않도록
host/scheme lowercase + query/fragment 제거 + trailing slash 제거를 일괄 처리.

poller (PR-06) 도 동일한 정책을 재사용해야 하므로 이 모듈만 import 한다.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop query+fragment, strip trailing slash.

    Root path ("/") 만 들어온 경우는 빈 문자열로 환원되지 않도록 그대로 둔다 —
    `https://news.hada.io/` 같은 입력이 host-only 표현과 충돌하지 않게 한다.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # query 와 fragment 는 dedupe key 판단에 의미가 없으므로 항상 제거.
    return urlunsplit((scheme, netloc, path, "", ""))


def dedupe_key(*, guid: str | None, url: str) -> str:
    """Prefer RSS `guid`; fall back to a normalized URL when guid is absent.

    plan.md 가 guid 우선을 못박은 이유: guid 는 발행자가 직접 부여하므로
    URL 변경(쿼리 추가, 경로 정리)이 있어도 동일 글로 안정적으로 식별 가능.

    kw-only scalar 시그니처로 두면 poller (PR-06) 가 Pydantic 의존 없이도
    동일 정책을 그대로 재사용할 수 있다.
    """
    if guid:
        return guid
    return normalize_url(url)
