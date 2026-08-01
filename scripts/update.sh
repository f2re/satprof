#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
git pull --ff-only origin main
"${SATPROF_VENV:-$ROOT/.venv}/bin/pip" install --no-build-isolation -e "$ROOT[all]"
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart satprof-worker.service satprof-web.service || true
fi
