# Project: useOpenClawtoMonitorGeekNews

## Project context

`plan.md` at the repo root is the authoritative architectural spec — read it for topology decisions,
environment variables, OpenClaw configuration, relay HTTP contract, and the implementation timeline.
Do not modify `plan.md`.

## Convention summary (full rules in @docs/convention.md)

- Toolchain: Ruff (format + lint), mypy --strict, pytest, pre-commit.
- Style: line length 100, double quotes, snake_case / PascalCase / UPPER_SNAKE_CASE.
- Comments: Korean OK for domain/Slack-output text; English for technical/API boundaries.
- Logging: stdlib logging + JSON formatter; required fields: timestamp, guid_or_url, dedupe_decision, openclaw_status, slack_delivered; never bare except.
- Commits: Conventional Commits; English subject ≤72 chars; body may be Korean.

@docs/convention.md
