#!/usr/bin/env bash
# cron 환경은 사용자 셸과 PATH/PYTHONPATH 가 다르다 — 명시적으로 프로젝트 루트로 cd 하고
# 모듈 경로를 보장한다. dotenv 는 main() 안에서 load_dotenv() 로 처리하므로 여기서는
# PATH/PYTHONPATH 만 export 한다 (PR-06 spec commit 5).
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"
exec .venv/bin/python -m poller.poll_geeknews
