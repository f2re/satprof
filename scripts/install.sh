#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SATPROF_VENV:-$ROOT/.venv}"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip setuptools wheel
"$VENV/bin/pip" install --no-build-isolation -e "$ROOT[all]"
mkdir -p "$ROOT/workspace"
if [[ ! -f "$ROOT/config/config.yaml" ]]; then cp "$ROOT/config/config.example.yaml" "$ROOT/config/config.yaml"; fi
printf 'SatProf установлен. Конфигурация: %s\n' "$ROOT/config/config.yaml"
printf 'Запуск: %s/bin/satprof-web --config %s/config/config.yaml\n' "$VENV" "$ROOT"
