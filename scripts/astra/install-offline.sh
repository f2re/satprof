#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

BUNDLE_ROOT=""
PREFIX="${SATPROF_PREFIX:-/opt/satprof}"
CONFIG="${SATPROF_CONFIG:-/etc/satprof/config.yaml}"
WORKSPACE="${SATPROF_WORKSPACE:-${PREFIX}/workspace}"
INSTALL_SATDUMP=1

usage() {
    cat <<EOF
Использование: scripts/astra/install-offline.sh --bundle-root PATH [параметры]

  --without-satdump   не устанавливать satdump-runtime из бандла
  --prefix PATH
  --config PATH
  --workspace PATH
EOF
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

if (( INSTALL_SATDUMP == 1 )) && [[ -x "${BUNDLE_ROOT}/satdump-runtime/bin/satdump" ]]; then
    RELEASE="/opt/satdump/releases/offline-$(date -u +%Y%m%dT%H%M%SZ)"
    as_root install -d -m 0755 "${RELEASE}"
    as_root rsync -a --delete "${BUNDLE_ROOT}/satdump-runtime/" "${RELEASE}/"
    atomic_symlink "${RELEASE}" /opt/satdump/current
    log_ok "SatDump runtime установлен: /opt/satdump/current"
fi

exec bash "${BUNDLE_ROOT}/satprof/scripts/astra/deploy.sh" \
    --source "${BUNDLE_ROOT}/satprof" \
    --prefix "${PREFIX}" \
    --config "${CONFIG}" \
    --workspace "${WORKSPACE}" \
    --wheelhouse "${BUNDLE_ROOT}/wheelhouse"
