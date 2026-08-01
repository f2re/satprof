#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

OUTPUT="${SATPROF_BUNDLE_OUTPUT:-${SATPROF_ROOT}/dist}"
SATDUMP_SOURCE="${SATDUMP_SOURCE:-/opt/SatDump}"
SATDUMP_PREFIX="${SATDUMP_PREFIX:-/opt/satdump/current}"
INCLUDE_SATDUMP_SOURCE=1
INCLUDE_SATDUMP_RUNTIME=1

usage() {
    cat <<EOF
Использование: scripts/astra/build-offline-bundle.sh [параметры]

  --output DIR                 каталог результата
  --without-satdump-source     не включать исходники SatDump
  --without-satdump-runtime    не включать собранный SatDump prefix

Запускайте на той же линии Astra/архитектуре, где будет установка: wheelhouse
собирается нативно и сопровождается SHA256SUMS и manifest.json.
EOF
}
while (( $# )); do
    case "$1" in
        --output) OUTPUT="$2"; shift 2 ;;
        --without-satdump-source) INCLUDE_SATDUMP_SOURCE=0; shift ;;
        --without-satdump-runtime) INCLUDE_SATDUMP_RUNTIME=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

PYTHON_BIN="$(find_python 2>/dev/null || true)"
[[ -n "${PYTHON_BIN}" ]] || die "Python 3.10+ не найден"
ASTRA_VERSION="$(detect_astra_version)"
ARCH="$(uname -m)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMMIT="$(git -C "${SATPROF_ROOT}" rev-parse --short=12 HEAD 2>/dev/null || printf source)"
NAME="satprof-offline-${ASTRA_VERSION}-${ARCH}-${STAMP}-${COMMIT}"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/satprof-bundle.XXXXXX")"
trap 'rm -rf "${STAGING}"' EXIT
ROOT="${STAGING}/${NAME}"
mkdir -p "${ROOT}/satprof" "${ROOT}/wheelhouse" "${OUTPUT}"

log_info "Копирование исходников SatProf"
rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude '.releases' --exclude 'workspace' \
    --exclude '__pycache__' --exclude 'dist' \
    "${SATPROF_ROOT}/" "${ROOT}/satprof/"

BUILDER="${STAGING}/builder-venv"
"${PYTHON_BIN}" -m venv "${BUILDER}"
"${BUILDER}/bin/pip" install --upgrade pip setuptools wheel
log_info "Сборка нативного wheelhouse"
"${BUILDER}/bin/pip" wheel --wheel-dir "${ROOT}/wheelhouse" "${ROOT}/satprof[all,test]"

if (( INCLUDE_SATDUMP_SOURCE == 1 )); then
    [[ -d "${SATDUMP_SOURCE}/.git" ]] || die "Исходники SatDump не найдены: ${SATDUMP_SOURCE}"
    branch="$(git -C "${SATDUMP_SOURCE}" symbolic-ref --short HEAD 2>/dev/null || true)"
    [[ "${branch}" == "release/1.2.2" ]] || die "SatDump должен быть на release/1.2.2"
    log_info "Добавление исходников SatDump"
    mkdir -p "${ROOT}/satdump-source"
    rsync -a --delete --exclude '.git' --exclude 'build' \
        "${SATDUMP_SOURCE}/" "${ROOT}/satdump-source/"
    git -C "${SATDUMP_SOURCE}" bundle create "${ROOT}/satdump-release-1.2.2.bundle" "release/1.2.2"
fi

if (( INCLUDE_SATDUMP_RUNTIME == 1 )); then
    [[ -x "${SATDUMP_PREFIX}/bin/satdump" ]] || die "Собранный SatDump не найден: ${SATDUMP_PREFIX}"
    log_info "Добавление установленного SatDump"
    mkdir -p "${ROOT}/satdump-runtime"
    rsync -aL --delete "${SATDUMP_PREFIX}/" "${ROOT}/satdump-runtime/"
fi

cat >"${ROOT}/manifest.json" <<EOF
{
  "schema": "satprof.offline-bundle/1",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "astra_version": "${ASTRA_VERSION}",
  "architecture": "${ARCH}",
  "python": "$(${PYTHON_BIN} -V 2>&1)",
  "satprof_commit": "$(git -C "${SATPROF_ROOT}" rev-parse HEAD 2>/dev/null || printf unknown)",
  "satdump_commit": "$(git -C "${SATDUMP_SOURCE}" rev-parse HEAD 2>/dev/null || printf unknown)",
  "includes_satdump_source": $([[ ${INCLUDE_SATDUMP_SOURCE} == 1 ]] && printf true || printf false),
  "includes_satdump_runtime": $([[ ${INCLUDE_SATDUMP_RUNTIME} == 1 ]] && printf true || printf false)
}
EOF

cat >"${ROOT}/install.sh" <<'SH2'
#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${ROOT}/satprof/scripts/astra/install-offline.sh" --bundle-root "${ROOT}" "$@"
SH2
chmod +x "${ROOT}/install.sh"

(
    cd "${ROOT}"
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
ARCHIVE="${OUTPUT}/${NAME}.tar.gz"
tar -C "${STAGING}" -czf "${ARCHIVE}" "${NAME}"
sha256sum "${ARCHIVE}" >"${ARCHIVE}.sha256"
log_ok "Офлайн-бандл создан: ${ARCHIVE}"
