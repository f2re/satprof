#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

PYTHON_VERSION="${PYTHON_VERSION:-3.11.15}"
PYTHON_SHA256="${PYTHON_SHA256:-272179ddd9a2e41a0fc8e42e33dfbdca0b3711aa5abf372d3f2d51543d09b625}"
PREFIX="${PYTHON_PREFIX:-/opt/satprof/toolchain/python}"
CACHE_DIR="${SATPROF_DOWNLOAD_CACHE:-/var/cache/satprof}"
ARCHIVE=""
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 2)}"
INSTALL_DEPS=0
RUN_TESTS=0

usage() {
    cat <<EOF
Использование: scripts/astra/bootstrap-python.sh [параметры]

  --archive PATH     использовать заранее скачанный Python-${PYTHON_VERSION}.tar.xz
  --prefix PATH      каталог установки (по умолчанию ${PREFIX})
  --install-deps     установить системные build-зависимости через APT
  --jobs N           параллелизм сборки
  --run-tests        выполнить полный CPython test suite

Архив проверяется по SHA-256. Сценарий не заменяет системный Python.
EOF
}

while (( $# )); do
    case "$1" in
        --archive) ARCHIVE="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --install-deps) INSTALL_DEPS=1; shift ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --run-tests) RUN_TESTS=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

[[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] || die "--jobs должен быть положительным числом"

if (( INSTALL_DEPS == 1 )); then
    as_root apt-get update
    as_root apt-get install -y \
        build-essential curl ca-certificates xz-utils \
        libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
        libffi-dev liblzma-dev libncursesw5-dev uuid-dev tk-dev
fi

for header in /usr/include/openssl/ssl.h /usr/include/zlib.h /usr/include/sqlite3.h; do
    [[ -r "${header}" ]] || die "Не найден ${header}; запустите с --install-deps"
done

as_root install -d -m 0755 "${CACHE_DIR}"
if [[ -z "${ARCHIVE}" ]]; then
    ARCHIVE="${CACHE_DIR}/Python-${PYTHON_VERSION}.tar.xz"
    if [[ ! -f "${ARCHIVE}" ]]; then
        command_exists curl || die "curl не найден; передайте --archive"
        temporary="${ARCHIVE}.part.$$"
        log_info "Загрузка CPython ${PYTHON_VERSION}"
        curl --fail --location --proto '=https' --tlsv1.2 \
            "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" \
            --output "${temporary}"
        as_root mv "${temporary}" "${ARCHIVE}"
    fi
fi
ARCHIVE="$(readlink -m "${ARCHIVE}")"
[[ -f "${ARCHIVE}" ]] || die "Архив не найден: ${ARCHIVE}"
printf '%s  %s\n' "${PYTHON_SHA256}" "${ARCHIVE}" | sha256sum -c -

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/satprof-python.XXXXXX")"
trap 'rm -rf "${BUILD_ROOT}"' EXIT

tar -xJf "${ARCHIVE}" -C "${BUILD_ROOT}"
SOURCE="${BUILD_ROOT}/Python-${PYTHON_VERSION}"
[[ -x "${SOURCE}/configure" ]] || die "Некорректный архив Python"

RELEASE_PREFIX="${PREFIX}-${PYTHON_VERSION}"
log_info "Конфигурация CPython в ${RELEASE_PREFIX}"
cd "${SOURCE}"
./configure \
    --prefix="${RELEASE_PREFIX}" \
    --with-ensurepip=install \
    --enable-shared \
    --with-lto=no
make -j "${JOBS}"
if (( RUN_TESTS == 1 )); then
    make test TESTOPTS='-j0 -x test_asyncio test_multiprocessing_forkserver'
fi
as_root make altinstall

as_root sh -c "printf '%s\n' '${RELEASE_PREFIX}/lib' > /etc/ld.so.conf.d/satprof-python.conf"
as_root ldconfig
"${RELEASE_PREFIX}/bin/python3.11" - <<'PY'
import ssl, sqlite3, zlib, venv
print('Python bootstrap OK:', ssl.OPENSSL_VERSION, sqlite3.sqlite_version)
PY

atomic_symlink "${RELEASE_PREFIX}" "${PREFIX}"
log_ok "Python ${PYTHON_VERSION} установлен: ${PREFIX}/bin/python3.11"
