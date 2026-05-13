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
- PR descriptions: MUST follow `docs/convention.md` §13 (Summary / Changes / Test plan). No other top-level sections, no tool-signature footers.

## PR description rule (enforced)

When the user asks for a PR description, PR body, "PR 본문/설명", or is about to run
`gh pr create`, you MUST emit the body in the exact format defined in `docs/convention.md`
§13 — three `##` sections in order: **Summary**, **Changes**, **Test plan**. Do not omit a
section, do not add extra top-level sections, do not append a "Generated with Claude Code"
footer. If a section has no content, write "N/A" with a one-line reason rather than
dropping the heading. If the user explicitly requests a deviation, comply but call out
that it diverges from §13.

@docs/convention.md
