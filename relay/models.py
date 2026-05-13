from __future__ import annotations

from pydantic import BaseModel, model_validator

_EXCERPT_MAX_LEN = 500


class WebhookPayload(BaseModel):
    """plan.md §"Relay HTTP Specification" 정의 — title/url 필수, 나머지는 선택."""

    title: str
    url: str
    guid: str | None = None
    excerpt: str | None = None
    published_at: str | None = None

    @model_validator(mode="after")
    def _truncate_excerpt(self) -> WebhookPayload:
        # plan.md: excerpt 길이초과는 서버측 truncate (거부 아님).
        if self.excerpt is not None and len(self.excerpt) > _EXCERPT_MAX_LEN:
            self.excerpt = self.excerpt[:_EXCERPT_MAX_LEN]
        return self
