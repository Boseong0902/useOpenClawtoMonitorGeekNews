# Engineering Playbook — useOpenClawtoMonitorGeekNews

This playbook governs all source code in this repository.
Toolchain is enforced at commit time via `pre-commit`; see `pyproject.toml` and `.pre-commit-config.yaml`.
Architectural decisions live in `plan.md` (read-only source of truth).

---

## 1. Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3.11+ | f-strings, `tomllib`, `match` available |
| Poller deps | `feedparser`, `requests`, `python-dotenv`, `sqlite3` (stdlib) | cron one-shot script |
| Relay deps | `fastapi`, `uvicorn[standard]`, `httpx`, `python-dotenv`, `pydantic` | systemd long-running |
| Type checker | mypy `--strict` | zero-Any policy; see §Type Annotations |
| Formatter + Linter | Ruff (replaces black, isort, flake8, bugbear, pyupgrade) | single config in `pyproject.toml` |
| Test framework | pytest + `httpx` test client + `respx` for mocking | see §Testing |
| Process supervision | systemd (relay), cron `/etc/cron.d/geeknews-poller` (poller) | GCP e2-micro VM |
| Agent gateway | OpenClaw `127.0.0.1:18789` via Socket Mode | never public |
| Chat surface | Slack `#assignment-geeknews` | channel ID in `.env` |
| State store | SQLite `poller/seen.sqlite` | single table `seen` |
| Logging | stdlib `logging` + `python-json-logger` | JSON lines, append-only |

---

## 2. Project Layout

```
useOpenClawtoMonitorGeekNews/
├── plan.md                        # Architectural spec (read-only)
├── pyproject.toml                 # Ruff + mypy config
├── .pre-commit-config.yaml        # Commit-time enforcement
├── CLAUDE.md                      # Claude Code session entry point
├── .env.example                   # Config template (real .env gitignored)
├── poller/
│   ├── poll_geeknews.py           # RSS fetch + dedupe + POST to relay
│   ├── seen.sqlite                # Dedupe store (gitignored)
│   ├── run.sh                     # Cron entrypoint (sources .env)
│   └── requirements.txt
├── relay/
│   ├── server.py                  # FastAPI app — auth, validate, log, call OpenClaw
│   ├── openclaw_client.py         # Thin httpx wrapper over OpenClaw hook
│   └── requirements.txt
├── deploy/
│   ├── relay.service              # systemd unit for relay
│   ├── geeknews-poller.cron       # Cron line for /etc/cron.d/
│   ├── openclaw.config.json       # OpenClaw config template
│   └── README.md                  # GCP VM step-by-step deployment
└── docs/
    ├── convention.md              # This file
    └── evidence/                  # Screenshots + log captures for submission
```

One-line purpose per directory:

- `poller/` — cron-driven RSS reader; owns the poller-side dedupe store
- `relay/` — always-on FastAPI webhook receiver; bridges poller → OpenClaw
- `deploy/` — systemd units, cron line, OpenClaw config template, VM setup guide
- `docs/` — engineering playbook + submission evidence

---

## 3. Code Style

All enforcement is automated by Ruff. The rules below describe the *intent* behind the config.

### Line length

100 characters. Prefer shorter; never go longer.

### Quotes

Double quotes everywhere. Ruff format enforces this — don't fight it.

### Naming

| Kind | Convention | Example |
|------|-----------|---------|
| Functions, variables | `snake_case` | `fetch_feed`, `relay_url` |
| Classes (Pydantic models, dataclasses) | `PascalCase` | `WebhookPayload`, `SeenRecord` |
| Module-level constants from env | `UPPER_SNAKE_CASE` | `RSS_FEED_URL`, `RELAY_BIND` |
| Private helpers | `_leading_underscore` | `_normalize_url` |

### Imports

Three groups separated by blank lines, in order:

1. stdlib
2. third-party
3. local (project-internal)

One import per line within each group. Ruff (isort-equivalent) enforces order automatically.

```python
# Good
import logging
import sqlite3

import feedparser
import httpx
from fastapi import FastAPI

from relay.openclaw_client import OpenClawClient
```

### Comment language

- **Korean** is encouraged for domain logic, agent prompt text, and anything related to the Slack output surface (이 프로젝트의 사용자 접점은 한국어).
- **English** for technical/API/library boundaries, function signatures, type annotations, and identifiers.

---

## 4. Type Annotations

- Every public function and method must have full parameter and return type annotations.
- `from __future__ import annotations` is allowed at the top of any file to enable forward references without quoting.
- `mypy --strict` is the enforcement level (see `pyproject.toml`).
- `Any` requires a `# type: ignore[type-arg]` (or more specific code) with a one-line justification comment directly above.

```python
# feedparser returns a loosely-typed MappingProxyType; Any is unavoidable here
entry_dict: Any = entry  # type: ignore[assignment]
```

- Third-party stubs missing (e.g. `feedparser`): use `ignore_missing_imports = true` in the per-module mypy override in `pyproject.toml` rather than scattering `# type: ignore` across the file.

---

## 5. Logging

### Setup

```python
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
```

File output goes to `LOG_FILE` from `.env` (default `/var/log/relay/relay.log`), opened in append mode.

### Required fields per log record

Every accepted relay request must produce one structured log line containing all five fields:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO 8601 UTC string | When the relay handled the request |
| `guid_or_url` | string | Dedupe key used for this item (RSS `<guid>` or normalized URL) |
| `dedupe_decision` | `"pass"` \| `"skip"` | Whether the relay forwarded to OpenClaw or short-circuited |
| `openclaw_status` | int \| `null` | HTTP status returned by OpenClaw; `null` if dedupe was `"skip"` |
| `slack_delivered` | bool \| `null` | Whether OpenClaw reported Slack delivery; `null` if not called |

Example:

```json
{
  "timestamp": "2026-05-06T09:42:00Z",
  "guid_or_url": "https://news.hada.io/topic?id=12345",
  "dedupe_decision": "pass",
  "openclaw_status": 200,
  "slack_delivered": true
}
```

### Log levels

| Level | Use |
|-------|-----|
| `DEBUG` | Internal state useful only when debugging (feed parse details, raw HTTP bodies) |
| `INFO` | Normal operational events (new item detected, relay accepted, Slack delivered) |
| `WARNING` | Recoverable anomalies (dedupe skip, retry attempt) |
| `ERROR` | Non-fatal failures (OpenClaw 5xx after retry, Slack delivery false) |
| `CRITICAL` | Startup failures that prevent the service from running |

---

## 6. Error Handling

### Exception specificity

- Never `except:` (bare).
- Never `except Exception` without immediately re-raising or logging with structured context.
- Catch the narrowest exception class available.

```python
# Bad
try:
    response = client.post(url, json=payload)
except Exception:
    pass

# Good
try:
    response = client.post(url, json=payload, timeout=10.0)
except httpx.TimeoutException:
    logger.error("OpenClaw request timed out", extra={"url": url})
    raise
except httpx.HTTPStatusError as exc:
    logger.error(
        "OpenClaw returned error status",
        extra={"status": exc.response.status_code, "url": url},
    )
    raise
```

### Retry policy for OpenClaw

Per `plan.md` §"Robustness items in scope":

- **1 retry** on transient OpenClaw 5xx.
- **Exponential backoff**: wait `2 ** attempt` seconds before retry (attempt 0 = first try, attempt 1 = retry after 2 s).
- After 1 retry, propagate the error as HTTP 502 to the caller.

```python
import time

MAX_RETRIES = 1

for attempt in range(MAX_RETRIES + 1):
    response = client.post(...)
    if response.status_code < 500:
        break
    if attempt < MAX_RETRIES:
        time.sleep(2 ** (attempt + 1))
else:
    raise OpenClawError(response.status_code)
```

### Propagation rules

- Relay: map exceptions to structured JSON error responses (`400`, `401`, `502`, `503`).
- Poller: log and exit with non-zero status so cron can detect failure.
- Never swallow errors silently.

---

## 7. Async

### When to use `async def`

| Component | Style | Rationale |
|-----------|-------|-----------|
| `relay/server.py` handlers | `async def` | FastAPI requires it; outbound I/O via `httpx.AsyncClient` |
| `relay/openclaw_client.py` | `async def` | Shares the relay's async event loop |
| `poller/poll_geeknews.py` | sync (`def`) | Cron one-shot; no concurrency benefit; simpler control flow |

### httpx client lifecycle (relay)

Reuse a single `httpx.AsyncClient` across all requests using FastAPI's lifespan context manager. Do **not** create a new client per request.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.openclaw_client = client
        yield

app = FastAPI(lifespan=lifespan)
```

### Poller

Use stdlib `requests` (sync). The poller runs for a few seconds and exits — asyncio overhead is not justified.

---

## 8. Testing

### Layout

```
tests/
├── conftest.py           # shared fixtures (test client, env patches)
├── relay/
│   ├── test_auth.py      # secret mismatch -> 401
│   ├── test_validation.py # missing fields -> 400
│   ├── test_dedupe.py    # 24h re-check -> no_reply
│   └── test_openclaw.py  # happy path + 5xx retry
└── poller/
    └── test_dedupe.py    # seen.sqlite insert + skip
```

### What must be tested (high-value paths)

- Auth: `X-Webhook-Secret` mismatch returns 401.
- Validation: missing required fields return 400 with the field name.
- Dedupe (relay): same guid within 24h returns `no_reply` without calling OpenClaw.
- Retry: OpenClaw 5xx triggers one retry; second 5xx returns 502.
- Happy path: valid payload, OpenClaw 200 with `matched`, relay returns 200 `{"status": "matched"}`.
- `NO_REPLY` path: valid payload, OpenClaw returns `no_reply`, relay returns 200 `{"status": "no_reply"}`.

### Nice-to-have (not required)

- Poller feed parsing with a fixture RSS XML string.
- End-to-end integration test against a local OpenClaw mock.

### Mocking outbound HTTP

Use `respx` to mock `httpx.AsyncClient` calls in relay tests. Do not make real network calls in tests.

```python
import respx
import httpx

@respx.mock
async def test_openclaw_happy_path(client):
    respx.post("http://127.0.0.1:18789/hooks/agent").mock(
        return_value=httpx.Response(200, json={"status": "matched"})
    )
    response = await client.post("/webhook/geeknews", ...)
    assert response.status_code == 200
```

### No coverage floor

Write tests for the high-value paths above. Numeric coverage thresholds are not enforced for this assignment.

---

## 9. Commits & Branching

### Commit format (Conventional Commits)

```
<type>(<scope>): <subject>

[optional body]
```

- `type`: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`
- `scope` (optional): `relay`, `poller`, `deploy`, `docs`, `config`
- `subject`: English, ≤72 characters, imperative mood, no trailing period
- `body` (optional): may be Korean for domain context; wrap at 72 chars

Examples:

```
feat(relay): add 24h dedupe re-check before OpenClaw call

fix(poller): handle missing RSS guid with normalized URL fallback

docs: add evidence screenshots for submission

chore(config): add ruff and mypy pre-commit hooks
```

### Branching (solo project)

This is a solo assignment repo. Work directly on `main` for day-to-day progress.
Create a feature branch only when experimenting with a breaking change:

```
feature/<short-description>   e.g.  feature/retry-backoff
```

---

## 10. Secrets & Env

### Loading

Both `poller` and `relay` load secrets from `.env` via `python-dotenv`:

```python
from dotenv import load_dotenv
load_dotenv()
```

Call `load_dotenv()` once at module top-level, before any `os.getenv()` calls.

### .env.example discipline

`.env.example` is committed and kept in sync with every variable the code reads.
Real values in `.env` are **gitignored** — verify `.gitignore` contains `.env`.

Required variables (from `plan.md` §"Environment Variables"):

```bash
# Source
RSS_FEED_URL=https://news.hada.io/rss
POLL_INTERVAL_MIN=5

# Internal endpoints
RELAY_URL=http://127.0.0.1:8080/webhook/geeknews
RELAY_BIND=127.0.0.1:8080
RELAY_SHARED_SECRET=<32-byte random>

# OpenClaw
OPENCLAW_URL=http://127.0.0.1:18789/hooks/agent
OPENCLAW_HOOK_TOKEN=<32-byte random>
OPENCLAW_AGENT_ID=gn-monitor

# Slack
SLACK_CHANNEL_ID=C0123456789

# Logging
LOG_FILE=/var/log/relay/relay.log
```

### Never-commit list

- `.env` (real secrets)
- `poller/seen.sqlite` (runtime state)
- `*.pyc`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`

### Secret generation

Use `python -c "import secrets; print(secrets.token_hex(32))"` to generate `RELAY_SHARED_SECRET` and `OPENCLAW_HOOK_TOKEN`. Never reuse them for each other.

---

## 11. Evidence & Screenshots

All submission evidence lives in `docs/evidence/`.

### File naming

```
YYYYMMDD-NN-description.png
```

- `YYYYMMDD`: capture date
- `NN`: two-digit sequence within the day (01, 02, ...)
- `description`: kebab-case, max 40 chars

Examples:

```
20260510-01-slack-channel-bot-member.png
20260510-02-three-matched-messages.png
20260510-03-systemctl-status-relay-openclaw.png
20260510-04-end-to-end-log-trace.png
20260510-05-noreply-log-line.png
```

### Required screenshots (per `plan.md` §"Evidence / Submission Strategy")

1. Slack workspace + `#assignment-geeknews` showing OpenClaw bot is a member.
2. At least 3 auto-delivered messages, each with all 4 fields (title / Korean summary / reason / link).
3. `systemctl status relay` and `systemctl status openclaw`, both active.
4. End-to-end trace: one cycle's poller log + relay log + OpenClaw log lines for a single GeekNews post.
5. (Recommended) A log line showing a non-matching post that returned `NO_REPLY`.

### Retention

Keep all evidence files in `docs/evidence/` until the assignment is submitted and graded. Do not delete intermediate captures — graders may ask for additional context.

---

## 12. CLAUDE.md Routing

### What the @-import does

`CLAUDE.md` at the repo root ends with `@docs/convention.md`. When Claude Code starts a session in this repo, it expands the `@`-import and loads the full content of this file into the session context. This means:

- Chat-time guidance (style, toolchain, logging rules) is always available to Claude without copy-pasting.
- The summary block in `CLAUDE.md` provides the gist even if import expansion is unavailable.

### How to add a new section

1. Add the section to this file (`docs/convention.md`) under a new `## N. Section Name` heading.
2. If it affects the 5-bullet summary in `CLAUDE.md`, update that summary too.
3. Do **not** modify `plan.md` — it is the architectural spec and is read-only for convention purposes.
4. Commit with type `docs`: `docs: add <section> to convention.md`.

### Routing diagram

```
CLAUDE.md (session entry point)
  └── @docs/convention.md (full Engineering Playbook)
        ├── pyproject.toml       (Ruff + mypy config — commit-time enforcement)
        └── .pre-commit-config.yaml  (pre-commit hooks)
```
