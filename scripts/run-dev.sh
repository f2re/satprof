#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
CONFIG="${SATPROF_CONFIG:-$ROOT/config/config.yaml}"
python -m satprof_calibrator.web.app --config "$CONFIG" --host 127.0.0.1 --port 8088 &
WEB_PID=$!
trap 'kill "$WEB_PID" 2>/dev/null || true' EXIT INT TERM
python -m satprof_calibrator.worker --config "$CONFIG"
