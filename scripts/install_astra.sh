#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/scripts/astra/common.sh"

PREFIX="${PREFIX:-/opt/satprof}"
CONFIG="${SATPROF_CONFIG:-/etc/satprof/config.yaml}"
WORKSPACE="${WORKSPACE:-${PREFIX}/workspace}"
WITH_SATDUMP=1
SATDUMP_INSTALL_DEPS=0
NO_START=0
BOOTSTRAP_PYTHON=0

usage() {
    cat <<EOF
Использование: scripts/install_astra.sh [параметры]

  --without-satdump       не собирать SatDump
  --satdump-install-deps  установить зависимости SatDump автоматически
  --bootstrap-python      принудительно собрать локальный Python 3.11
  --no-start              не запускать службы после установки
  --prefix PATH
  --config PATH
  --workspace PATH
EOF
}
while (( $# )); do
    case "$1" in
        --without-satdump) WITH_SATDUMP=0; shift ;;
        --satdump-install-deps) SATDUMP_INSTALL_DEPS=1; shift ;;
        --bootstrap-python) BOOTSTRAP_PYTHON=1; shift ;;
        --no-start) NO_START=1; shift ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --workspace) WORKSPACE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

ASTRA_VERSION="$(detect_astra_version)"
[[ "${ASTRA_VERSION}" == "1.6" || "${ASTRA_VERSION}" == "1.7" ]] \
    || die "Поддерживаются Astra Linux 1.6 и 1.7"

log_info "Установка системных зависимостей"
as_root apt-get update
as_root apt-get install -y \
    ca-certificates curl git rsync tar xz-utils pkg-config \
    build-essential gfortran cmake \
    python3 python3-venv python3-dev \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev libncursesw5-dev uuid-dev \
    libopenblas-dev liblapack-dev \
    libeccodes0 libeccodes-dev libeccodes-tools \
    libnetcdf-dev libhdf5-dev libjpeg-dev libtiff-dev

if ! id satprof >/dev/null 2>&1; then
    as_root useradd --system --home "${PREFIX}" --shell /usr/sbin/nologin satprof
fi
as_root install -d -o satprof -g satprof -m 0750 "${WORKSPACE}"
as_root install -d -o root -g satprof -m 0750 "$(dirname "${CONFIG}")"

if (( BOOTSTRAP_PYTHON == 1 )) || ! find_python >/dev/null 2>&1; then
    log_info "Системный Python недостаточен; сборка локального CPython"
    bash "${ROOT}/scripts/astra/bootstrap-python.sh" --install-deps
fi

if (( WITH_SATDUMP == 1 )); then
    SATDUMP_ARGS=()
    (( SATDUMP_INSTALL_DEPS == 1 )) && SATDUMP_ARGS+=(--install-deps)
    bash "${ROOT}/scripts/astra/build-satdump.sh" "${SATDUMP_ARGS[@]}"
fi

DEPLOY_ARGS=(
    --source "${ROOT}"
    --prefix "${PREFIX}"
    --config "${CONFIG}"
    --workspace "${WORKSPACE}"
)
(( NO_START == 1 )) && DEPLOY_ARGS+=(--no-start)
bash "${ROOT}/scripts/astra/deploy.sh" "${DEPLOY_ARGS[@]}"

log_ok "Установка завершена"
printf 'Интерфейс: http://127.0.0.1:8088\n'
printf 'Диагностика: %s/scripts/astra/healthcheck.sh --deep\n' "${PREFIX}/current"
