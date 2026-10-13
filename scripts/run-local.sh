#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -d .venv ]]; then
  python -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements-server.txt -r requirements-client.txt
fi
.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000 &
SERVER_PID=$!
cleanup(){ kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
sleep 1
.venv/bin/python -m voxis_client.main
