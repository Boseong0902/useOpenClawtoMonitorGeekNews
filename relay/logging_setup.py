from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Literal

from pythonjsonlogger import jsonlogger

_CONFIGURED_FLAG = "_relay_json_logging_configured"


def configure_logging() -> logging.Logger:
    """Idempotently install JSON formatter + file/console handlers on the root logger."""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return root

    root.setLevel(logging.INFO)
    # python-json-logger does not re-export JsonFormatter from the package root
    # under strict mypy; access via the submodule attribute.
    formatter = jsonlogger.JsonFormatter()  # type: ignore[attr-defined]

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    log_file = os.environ.get("LOG_FILE", "relay.log")
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


def log_relay_decision(
    *,
    logger: logging.Logger,
    guid_or_url: str,
    dedupe_decision: Literal["pass", "skip"],
    openclaw_status: int | None,
    slack_delivered: bool | None,
) -> None:
    """plan.md §"Server-side behavior" 의 필수 5개 필드를 한 줄 JSON 으로 남긴다."""
    logger.info(
        "relay_decision",
        extra={
            "timestamp": datetime.now(UTC).isoformat(),
            "guid_or_url": guid_or_url,
            "dedupe_decision": dedupe_decision,
            "openclaw_status": openclaw_status,
            "slack_delivered": slack_delivered,
        },
    )
