#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-/opt/satprof}"
CONFIG_DIR="${CONFIG_DIR:-/etc/satprof}"
WORKSPACE="${WORKSPACE:-$PREFIX/workspace}"
PYTHON="${PYTHON:-python3}"

"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit('Требуется Python 3.10 или новее')
PY

sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential libeccodes0 libeccodes-dev libeccodes-tools rsync
sudo useradd --system --home "$PREFIX" --shell /usr/sbin/nologin satprof 2>/dev/null || true
sudo install -d -o satprof -g satprof "$PREFIX" "$WORKSPACE"
sudo install -d -o root -g root "$CONFIG_DIR"
sudo rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'workspace' --exclude '__pycache__' "$ROOT/" "$PREFIX/"
sudo -u satprof "$PYTHON" -m venv "$PREFIX/.venv"
sudo -u satprof "$PREFIX/.venv/bin/pip" install --upgrade pip setuptools wheel
sudo -u satprof "$PREFIX/.venv/bin/pip" install --no-build-isolation -e "$PREFIX[all]"
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
  sudo cp "$PREFIX/config/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi
sudo sed -i "s|^workspace:.*|workspace: $WORKSPACE|" "$CONFIG_DIR/config.yaml"
sudo cp "$PREFIX/systemd/satprof.env.example" "$CONFIG_DIR/satprof.env"
sudo sed -i "s|SATPROF_CONFIG=.*|SATPROF_CONFIG=$CONFIG_DIR/config.yaml|" "$CONFIG_DIR/satprof.env"
sudo cp "$PREFIX/systemd/satprof-web.service" "$PREFIX/systemd/satprof-worker.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now satprof-worker.service satprof-web.service
printf 'SatProf установлен. Откройте http://127.0.0.1:8088\n'
