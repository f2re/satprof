#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

BUNDLE_ROOT=""
PREFIX="${SATPROF_PREFIX:-/opt/satprof}"
CONFIG="${SATPROF_CONFIG:-/etc/satprof/config.yaml}"
WORKSPACE="${SATPROF_WORKSPACE:-${PREFIX}/workspace}"
INSTALL_SATDUMP=1

usage() {
    cat <<EOF2
Использование: scripts/astra/install-offline.sh --bundle-root PATH [параметры]

  --without-satdump   не устанавливать source/runtime SatDump из бандла
  --prefix PATH
  --config PATH
  --workspace PATH
EOF2
}
while (( $# )); do
    case "$1" in
        --bundle-root) BUNDLE_ROOT="$2"; shift 2 ;;
        --without-satdump) INSTALL_SATDUMP=0; shift ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --workspace) WORKSPACE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done
[[ -n "${BUNDLE_ROOT}" ]] || die "Укажите --bundle-root"
BUNDLE_ROOT="$(readlink -m "${BUNDLE_ROOT}")"
[[ -f "${BUNDLE_ROOT}/SHA256SUMS" ]] || die "SHA256SUMS отсутствует"
(
    cd "${BUNDLE_ROOT}"
    sha256sum -c SHA256SUMS
)
[[ -f "${BUNDLE_ROOT}/satprof/pyproject.toml" ]] || die "Исходники SatProf отсутствуют"
[[ -d "${BUNDLE_ROOT}/wheelhouse" ]] || die "wheelhouse отсутствует"

if (( INSTALL_SATDUMP == 1 )); then
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    SOURCE_RELEASE="/opt/satdump/sources/offline-${STAMP}"
    RUNTIME_RELEASE="/opt/satdump/releases/satdump-offline-${STAMP}"
    PREVIOUS_SOURCE="$(readlink -f /opt/SatDump 2>/dev/null || true)"
    PREVIOUS_RUNTIME="$(readlink -f /opt/satdump/current 2>/dev/null || true)"
    as_root install -d -m 0755 /opt/satdump/sources /opt/satdump/releases

    if [[ -f "${BUNDLE_ROOT}/satdump-release-1.2.2.bundle" ]]; then
        log_info "Установка исходников SatDump из проверенного Git bundle"
        as_root git clone --branch release/1.2.2 \
            "${BUNDLE_ROOT}/satdump-release-1.2.2.bundle" "${SOURCE_RELEASE}"
    elif [[ -d "${BUNDLE_ROOT}/satdump-source/scripts/astra" ]]; then
        log_warn "Git bundle отсутствует; устанавливается source snapshot без истории"
        as_root install -d -m 0755 "${SOURCE_RELEASE}"
        as_root rsync -a --delete "${BUNDLE_ROOT}/satdump-source/" "${SOURCE_RELEASE}/"
    else
        die "В бандле отсутствуют исходники SatDump"
    fi

    if [[ -e /opt/SatDump && ! -L /opt/SatDump ]]; then
        LEGACY="/opt/satdump/sources/legacy-${STAMP}"
        log_warn "Существующий /opt/SatDump переносится в ${LEGACY}"
        as_root mv /opt/SatDump "${LEGACY}"
        PREVIOUS_SOURCE="${LEGACY}"
    fi
    atomic_symlink "${SOURCE_RELEASE}" /opt/SatDump

    [[ -x "${BUNDLE_ROOT}/satdump-runtime/bin/satdump" ]] \
        || die "В бандле отсутствует исполняемый SatDump runtime"
    as_root install -d -m 0755 "${RUNTIME_RELEASE}"
    as_root rsync -a --delete "${BUNDLE_ROOT}/satdump-runtime/" "${RUNTIME_RELEASE}/"
    atomic_symlink "${RUNTIME_RELEASE}" /opt/satdump/current

    if ! bash /opt/SatDump/scripts/astra/run.sh \
        --prefix /opt/satdump/current -- version; then
        log_error "Офлайн SatDump не прошёл smoke-test; возврат предыдущих ссылок"
        [[ -n "${PREVIOUS_SOURCE}" && -d "${PREVIOUS_SOURCE}" ]] \
            && atomic_symlink "${PREVIOUS_SOURCE}" /opt/SatDump
        [[ -n "${PREVIOUS_RUNTIME}" && -d "${PREVIOUS_RUNTIME}" ]] \
            && atomic_symlink "${PREVIOUS_RUNTIME}" /opt/satdump/current
        exit 1
    fi
    log_ok "SatDump source/runtime установлены"
fi

exec bash "${BUNDLE_ROOT}/satprof/scripts/astra/deploy.sh" \
    --source "${BUNDLE_ROOT}/satprof" \
    --prefix "${PREFIX}" \
    --config "${CONFIG}" \
    --workspace "${WORKSPACE}" \
    --wheelhouse "${BUNDLE_ROOT}/wheelhouse"
