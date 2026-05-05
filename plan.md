# Goal

Implement assignment:

- onboard an LLM agent into a chat app
- receive wanted info automatically on a continuing basis
- capture evidence/screenshots for submission

Concrete solution finalized after deep interview:

- source: GeekNews **RSS polling** (no public outbound webhook exists; we synthesize the webhook ourselves)
- agent runtime: OpenClaw on GCP VM
- chat app: Slack (Socket Mode)
- behavior: cron poller -> internal HTTP webhook -> relay -> OpenClaw `/hooks/agent` -> Slack

Hard constraint:

- agent platform is fixed to OpenClaw
- do not replace OpenClaw with direct OpenAI SDK calls, LangChain-only flow, n8n AI node, custom bot-only logic, or other agent runtimes

# Requirement Interpretation

`onboarding` in assignment context means:

- connect an LLM-powered agent into a chat app the user actually uses
- make the agent visibly act inside that chat app
- show that received chat content is produced/filtered by LLM logic, not plain fixed automation

For grading, these 4 things should be visually obvious:

1. chat app exists and bot/app is present
2. messages are automatically delivered there
3. LLM did judgment/summarization
4. this is ongoing automation, not one-off manual execution

Important nuance:

- plain backend automation that only forwards raw links to Slack is weaker
- Slack messages MUST include LLM output, not only source URL
- the OpenClaw agent must visibly produce 4 fields: title / Korean summary / selection reason / link

# Trigger Source Decision

GeekNews (news.hada.io) does **not** publish an outbound webhook API. Only RSS is publicly available.

Decision:

- adopt RSS polling at 5-minute intervals
- the poller acts as the "internal webhook emitter" and POSTs newly-detected posts to the relay
- this preserves the `source -> relay -> OpenClaw -> Slack` topology while being grounded in what GeekNews actually offers

RSS feed URL (verified shape):

- `https://news.hada.io/rss`

Polling cadence:

- `*/5 * * * *` via Linux cron — i.e. once every 5 minutes
- worst-case Slack arrival latency = 5 min poll cycle + ~5s LLM = ~5 min after RSS publishes

Dedupe key:

- prefer RSS `<guid>` if present
- fallback: normalized URL (strip query string, lowercase host)

# Architectural Decisions

Topology (final):

```
[GeekNews RSS]
     │ (HTTP GET, every 5 min)
     ▼
[poller (cron)]  ──── seen.sqlite ────┐
     │                                  │ (dedupe)
     │ POST /webhook/geeknews           │
     │   X-Webhook-Secret: ***          │
     ▼                                  │
[relay (FastAPI on systemd, :8080)] ───┘
     │ POST /hooks/agent
     │   Authorization: Bearer ***
     ▼
[OpenClaw (localhost:18789, systemd)]
     │ Slack Socket Mode (xoxb / xapp)
     ▼
[Slack #assignment-geeknews]
```

Why a separate relay (not "poller calls OpenClaw directly"):

- the assignment narrative requires a visible "webhook arrives -> LLM judges -> chat app receives" flow; the relay's HTTP POST log is direct evidence
- isolates RSS-shape changes from OpenClaw configuration
- centralizes dedupe re-check, request shaping, and append-only logging
- keeps OpenClaw on localhost only — never publicly reachable

Process inventory (Option B):

- `poller`: cron-driven Python script, runs every 5 min, exits after one pass
- `relay`: long-running FastAPI server under systemd, listens on `127.0.0.1:8080`
- `OpenClaw`: long-running gateway under systemd, listens on `127.0.0.1:18789`
- public ingress: only optional Nginx/Caddy on `:80/:443` if external HTTPS is needed (not strictly required since GeekNews doesn't actually call us)

# Hosting Decision

- deploy on GCP VM (e2-micro is sufficient — no public ingress traffic to handle)
- user already has GCP free credits
- single zone, no HA, no autoscaling — assignment-grade

Operational stance:

- public ingress only for optional reverse proxy on `80/443`
- OpenClaw bound to `127.0.0.1` exclusively
- relay bound to `127.0.0.1` (the poller is on the same VM, so loopback is enough)
- if any public exposure becomes necessary later (e.g. external smoke testing), front with Caddy

# Stack and Tooling

Language: **Python 3.11+**

Per-component packages:

- poller: `feedparser`, `requests`, `python-dotenv`, stdlib `sqlite3`
- relay: `fastapi`, `uvicorn[standard]`, `httpx`, `python-dotenv`, `pydantic`

State store:

- single SQLite file `poller/seen.sqlite`, single table `seen`

Process supervision:

- systemd unit for relay (always-on)
- cron `/etc/cron.d/geeknews-poller` for poller (single-shot, every 5 min)

# OpenClaw Role and Configuration

OpenClaw is used as:

- always-on agent gateway (systemd-managed)
- Slack-connected agent surface via Socket Mode
- internal LLM execution point triggered by HTTP hook from the relay

Constraint:

- OpenClaw is not optional; other components exist to support OpenClaw, not to replace it
- bound to `127.0.0.1:18789` — never public

Conceptual config (`deploy/openclaw.config.json`):

```json
{
  "channels": {
    "slack": {
      "enabled": true,
      "mode": "socket",
      "appToken": "xapp-...",
      "botToken": "xoxb-...",
      "groupPolicy": "allowlist",
      "channels": {
        "C0123456789": {
          "requireMention": false
        }
      }
    }
  },
  "hooks": {
    "enabled": true,
    "token": "<long-random-secret>",
    "path": "/hooks",
    "allowedAgentIds": ["gn-monitor"]
  }
}
```

Notes:

- `OPENCLAW_HOOK_TOKEN` must NOT be reused as the general gateway auth token
- target Slack channel using explicit `channel:<id>` form when sending

# Slack Integration Decision

Use Slack via OpenClaw Socket Mode.

Why Socket Mode:

- no public callback URL needed (matches our "OpenClaw stays private" stance)
- simpler on a single VM
- official OpenClaw default

Slack config:

- one dedicated channel: `#assignment-geeknews`
- channel ID stored in `.env` as `SLACK_CHANNEL_ID=C0123456789`
- allowlist this channel only
- `requireMention: false` so the agent can post unprompted

# Agent Behavior Spec

Dedicated OpenClaw agent: `gn-monitor`.

Mission:

- input: one GeekNews post (title, url, excerpt, optional published_at)
- decide whether the post matches the user's interest
- if matched: emit a concise Korean Slack message in the exact format below
- if not matched: emit exactly the token `NO_REPLY` and nothing else

Match criteria (final, after Round 4 of deep interview):

1. AI / LLM / agent-related new tech (model releases, agent frameworks, MCP, etc.)
2. Developer productivity tools that the user could **realistically adopt and keep using** (CLIs, editor plugins, automation scripts, workflow tools) — emphasis on "would actually keep using", not generic dev news

Exact agent prompt:

```text
당신은 GeekNews 새 게시글을 검토하는 분류·요약 에이전트입니다.
다음 기준 중 하나에라도 해당하면 매칭으로 간주합니다:
1) AI / LLM / 에이전트 관련 신기술 (모델 발표, 새 에이전트 프레임워크, MCP 등)
2) "실제로 채택해서 계속 쓸 수 있는" 개발 생산성 도구
   (예: CLI, 에디터 플러그인, 자동화 스크립트, 워크플로우 도구)

매칭이면 정확히 다음 포맷의 한국어 메시지만 출력하세요. 군더더기 금지.

[GeekNews Match]
제목: <원문 제목>
요약: <한국어 1-2줄>
선정 이유: <한국어 1줄, 위 두 기준 중 어느 것에 어떻게 해당하는지>
링크: <원문 URL>

매칭이 아니면 다른 어떤 텍스트도 쓰지 말고 정확히 다음 토큰만 출력하세요:
NO_REPLY
```

Hard rules:

- no irrelevant chatter, no preamble, no postscript
- if not matched: exactly the token `NO_REPLY` (no whitespace, no quotes, no extra lines)
- output language: Korean for matched messages

# Hook Invocation Pattern

Relay calls OpenClaw's agent hook (not wake hook) because we need an isolated turn with explicit message + delivery target.

Concrete request from relay:

```bash
curl -X POST http://127.0.0.1:18789/hooks/agent \
  -H "Authorization: Bearer $OPENCLAW_HOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agentId": "gn-monitor",
    "name": "GeekNews",
    "message": "Title: <title>\nURL: <url>\nExcerpt: <excerpt>",
    "deliver": "announce",
    "channel": "slack",
    "to": "channel:'"$SLACK_CHANNEL_ID"'"
  }'
```

Notes:

- the `message` body is intentionally compact — the matching/formatting rules are in the agent prompt, not duplicated per request
- `deliver: announce` causes OpenClaw to push to Slack only when the agent emits non-`NO_REPLY` output

# Repository Layout

```
hongik_SE/
├── md/
│   └── plan.md                       # this document
├── poller/
│   ├── poll_geeknews.py              # RSS fetch + diff + POST to relay
│   ├── seen.sqlite                   # dedupe store (gitignored)
│   ├── run.sh                        # cron entrypoint (sources .env)
│   └── requirements.txt
├── relay/
│   ├── server.py                     # FastAPI app
│   ├── openclaw_client.py            # thin HTTP wrapper over OpenClaw hook
│   ├── requirements.txt
│   └── .env.example                  # config template (real .env gitignored)
├── deploy/
│   ├── relay.service                 # systemd unit for relay
│   ├── geeknews-poller.cron          # cron line, installed to /etc/cron.d/
│   ├── openclaw.config.json          # OpenClaw config template
│   └── README.md                     # GCP VM step-by-step deployment
├── docs/
│   └── evidence/                     # screenshots + log captures for submission
└── .omc/specs/
    └── deep-interview-geeknews-openclaw-slack.md   # source-of-truth spec
```

# Environment Variables

Stored in `.env` (gitignored). Loaded by both poller and relay.

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

# Relay HTTP Specification

```
POST /webhook/geeknews
  Headers:
    Content-Type: application/json
    X-Webhook-Secret: <must equal RELAY_SHARED_SECRET>
  Body:
    {
      "title":   "string, required",
      "url":     "string, required",
      "guid":    "string, optional (falls back to normalized url)",
      "excerpt": "string, max 500 chars after server-side truncate",
      "published_at": "ISO 8601 string, optional"
    }
  Responses:
    200 {"status": "matched"|"no_reply", "openclaw_status": <int>}
    400 {"error": "<missing field name>"}    # validation
    401 {"error": "auth"}                    # secret mismatch
    502 {"error": "openclaw"}                # OpenClaw call failed
    503 {"error": "slack"}                   # OpenClaw delivered=false
```

Server-side behavior:

- log every accepted request with: timestamp, guid/url, dedupe-decision, openclaw_status, slack_delivered
- dedupe re-check: if the same `guid`/normalized-url has already produced a `matched` result in the last 24h, return `200 {"status": "no_reply", "reason": "dedupe"}` without calling OpenClaw

# SeenStore Schema

```sql
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,        -- guid or normalized url
    title TEXT,
    seen_at TEXT NOT NULL,       -- ISO 8601 UTC
    relay_status INTEGER,        -- last HTTP status returned by relay
    matched INTEGER              -- 1 if relay reported "matched", 0 otherwise
);
CREATE INDEX IF NOT EXISTS idx_seen_at ON seen(seen_at);
```

# Why Relay Exists (Reconfirmed)

Even though the trigger is internal (not a real external webhook), the relay still earns its place:

- gives the assignment a visible, log-grepable "webhook arrives -> LLM judges -> chat app delivers" trail
- isolates RSS payload changes from OpenClaw config
- separates dedupe responsibility from polling responsibility (poller has its own seen store; relay has 24h re-check window)
- keeps OpenClaw fully on localhost, behind the only thing that ever talks to it
- single-purpose service with clean responsibilities — easier to demo and explain in the report

# Evidence / Submission Strategy

Screenshots required (4 minimum):

1. Slack workspace + `#assignment-geeknews` channel showing OpenClaw bot is a member
2. At least 3 auto-delivered messages, each containing all 4 fields (title / Korean summary / reason / link)
3. `systemctl status relay` and `systemctl status openclaw` outputs showing both services active
4. End-to-end trace: one cycle's poller log + relay log + OpenClaw log lines stitched together for a single GeekNews post (visually proves: detect -> POST -> hook -> Slack)

Negative-test screenshot (recommended):

5. Log line showing a non-matching post that the agent returned `NO_REPLY` for (proves LLM filtering, not blind forwarding)

Report narrative (one sentence):

`Slack 채널에 LLM 기반 에이전트를 연동하여 GeekNews 신규 게시글 발생 시 내용을 자동 분석하고, 관심 조건에 부합하는 경우 요약과 함께 채널로 전송하도록 온보딩하였다.`

# Security / Ops Constraints

Required:

- expose only `80/443` publicly (and only if a reverse proxy is added — not required by default)
- relay and OpenClaw both bound to `127.0.0.1`
- Slack/OpenClaw secrets in `.env`, never committed
- run relay as systemd-managed service on GCP VM
- run OpenClaw as systemd-managed service on GCP VM
- cron job runs as a non-root system user (e.g. `geeknews`) with read access to the project directory only

Robustness items in scope:

- dedupe store (poller-side seen.sqlite + relay-side 24h re-check)
- 1 retry with exponential backoff on transient OpenClaw 5xx
- append-only logs for evidence

Robustness items out of scope (explicitly):

- metrics, dashboards, alerting
- distributed tracing
- HA, failover, multi-AZ
- queueing systems (RabbitMQ/Kafka/etc.) — direct HTTP is sufficient at GeekNews's volume

# Acceptance Criteria

Functional:

- [ ] When a new post appears in GeekNews RSS, a Slack message arrives within 5 min (worst case 10 min)
- [ ] The same post is never delivered twice (dedupe verified)
- [ ] Non-matching posts produce zero Slack messages (`NO_REPLY` path)
- [ ] Every Slack message contains all 4 fields: title / Korean summary / reason / link
- [ ] Match decisions are humanly defensible against the two stated criteria
- [ ] After a VM reboot, both services come back automatically (no manual steps)

Evidence (for submission):

- [ ] Screenshot 1: Slack channel + bot membership
- [ ] Screenshot 2: ≥3 matched auto-messages with all 4 fields
- [ ] Screenshot 3: `systemctl status` for relay + OpenClaw, both active
- [ ] Screenshot 4: end-to-end log trace for one cycle
- [ ] Screenshot 5 (recommended): a `NO_REPLY` log line for a non-matching post

Negative:

- [ ] An off-topic GeekNews post (e.g. lifestyle, gossip) does NOT reach Slack and the relay log shows `no_reply` for it

# Non-Goals

Not required for assignment success:

- full bidirectional conversational Slack bot
- slash commands
- mention-required interactive flows
- complex multi-agent routing
- public exposure of Slack HTTP event callbacks
- cron polling replacement for "real" webhook trigger (RSS polling IS the trigger)

Not allowed from current decision baseline:

- replacing OpenClaw with a simpler non-agent pipeline
- removing the relay (collapsing poller -> OpenClaw direct call) — would break the topology narrative

# Implementation Priority (1-month plan, generous slack)

Suggested execution order:

1. **Day 1–2** — Provision GCP e2-micro VM, install Python 3.11, install OpenClaw, verify OpenClaw runs as systemd service on `127.0.0.1:18789`
2. **Day 3** — Create Slack workspace channel `#assignment-geeknews`, generate `xapp-`/`xoxb-` tokens, wire OpenClaw Socket Mode, send one manual hello message to verify Slack connectivity
3. **Day 4–5** — Build `relay/server.py`: auth + payload validation + OpenClaw client + structured logging + 24h dedupe table
4. **Day 6–7** — Build `poller/poll_geeknews.py`: feedparser fetch + dedupe via `seen.sqlite` + POST to relay + retry logic + cron entrypoint script
5. **Day 8** — Install systemd unit for relay, install cron line for poller, reboot VM and verify auto-recovery
6. **Day 9–10** — Tune `gn-monitor` agent prompt with 5–10 real GeekNews posts (mix of matching and non-matching) until matches/non-matches are correctly classified
7. **Day 11–12** — Run negative-test posts, capture all 5 screenshots, gather log excerpts, file under `docs/evidence/`
8. **Day 13+** — Write the assignment report, polish documentation, retain remaining buffer

# References Consulted

- OpenClaw Slack docs: `https://docs.openclaw.ai/channels/slack`
- OpenClaw scheduled tasks / hooks docs: `https://docs.openclaw.ai/automation/cron-jobs`
- OpenClaw message CLI docs: `https://docs.openclaw.ai/cli/message`
- OpenClaw agents docs: `https://docs.openclaw.ai/cli/agents`
- OpenClaw session/no-reply docs: `https://docs.openclaw.ai/reference/session-management-compaction`
- Deep interview spec: `.omc/specs/deep-interview-geeknews-openclaw-slack.md`

# Immediate Next Step For Future Session

Most likely next practical task:

- start coding `relay/server.py` (FastAPI skeleton + auth + validation), then `poller/poll_geeknews.py`

If continuing from this file, assume:

- RSS polling is the fixed trigger (no real GeekNews webhook exists)
- Slack is the fixed target chat app, accessed via OpenClaw Socket Mode
- OpenClaw is the fixed and mandatory LLM gateway/runtime
- GCP VM is the fixed deployment target
- Stack is locked: Python 3.11 + FastAPI + feedparser + SQLite + cron + systemd
