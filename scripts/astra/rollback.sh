#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

PREFIX="${SATPROF_PREFIX:-/opt/satprof}"
TARGET=""
HEALTH_TIMEOUT="${SATPROF_HEALTH_TIMEOUT:-60}"

usage() {
    cat <<EOF
Использование: scripts/astra/rollback.sh [--prefix PATH] [--to RELEASE_ID]

Без --to выбирается предыдущий по времени release. Переключение current/.venv
атомарно; после перезапуска выполняется /health/ready.
EOF
}
while (( $# )); do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --to) TARGET="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

RELEASES="${PREFIX}/.releases"
CURRENT="$(readlink -f "${PREFIX}/current" 2>/dev/null || true)"
[[ -d "${RELEASES}" ]] || die "Каталог релизов не найден: ${RELEASES}"
if [[ -n "${TARGET}" ]]; then
    CANDIDATE="${RELEASES}/$(safe_release_name "${TARGET}")"
else
    CANDIDATE="$(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
        | sort -nr | awk -v current="${CURRENT}" '$2 != current {print $2; exit}')"
fi
[[ -n "${CANDIDATE}" && -d "${CANDIDATE}/.venv" ]] || die "Подходящий предыдущий релиз не найден"

OLD_CURRENT="${CURRENT}"
OLD_VENV="$(readlink -f "${PREFIX}/.venv" 2>/dev/null || true)"
atomic_symlink "${CANDIDATE}" "${PREFIX}/current"
atomic_symlink "${CANDIDATE}/.venv" "${PREFIX}/.venv"
as_root systemctl restart satprof-worker.service satprof-web.service

CONFIG="${SATPROF_CONFIG:-/etc/satprof/config.yaml}"
PORT="$(${CANDIDATE}/.venv/bin/python - "${CONFIG}" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding='utf-8') as stream:
    cfg=yaml.safe_load(stream) or {}
print(int(cfg.get('web',{}).get('port',8088)))
PY
)"
if ! wait_http "http://127.0.0.1:${PORT}/health/ready" "${HEALTH_TIMEOUT}"; then
    log_error "Откатный релиз не прошёл readiness; возвращается исходное состояние"
    [[ -n "${OLD_CURRENT}" ]] && atomic_symlink "${OLD_CURRENT}" "${PREFIX}/current"
    [[ -n "${OLD_VENV}" ]] && atomic_symlink "${OLD_VENV}" "${PREFIX}/.venv"
    as_root systemctl restart satprof-worker.service satprof-web.service
    exit 1
fi
log_ok "Выполнен откат на $(basename "${CANDIDATE}")"
