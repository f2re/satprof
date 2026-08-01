#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

CONFIG="${SATPROF_CONFIG:-/etc/satprof/config.yaml}"
PREFIX="${SATPROF_PREFIX:-/opt/satprof}"
DEEP=0
PROMETHEUS=0

while (( $# )); do
    case "$1" in
        --config) CONFIG="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --deep) DEEP=1; shift ;;
        --prometheus) PROMETHEUS=1; shift ;;
        -h|--help)
            printf 'Использование: %s [--deep] [--prometheus] [--config PATH]\n' "$0"
            exit 0
            ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

MONITOR="${PREFIX}/.venv/bin/satprof-monitor"
[[ -x "${MONITOR}" ]] || die "satprof-monitor не найден: ${MONITOR}"
ARGS=(--config "${CONFIG}" --write-snapshot --allow-degraded)
(( DEEP == 1 )) && ARGS+=(--deep)
(( PROMETHEUS == 1 )) && ARGS+=(--prometheus)
exec "${MONITOR}" "${ARGS[@]}"
