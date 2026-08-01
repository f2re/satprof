#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

SOURCE="${SATDUMP_SOURCE:-/opt/SatDump}"
REPOSITORY="${SATDUMP_REPOSITORY:-https://github.com/f2re/SatDump.git}"
BRANCH="${SATDUMP_BRANCH:-release/1.2.2}"
EXPECTED_COMMIT="${SATDUMP_EXPECTED_COMMIT:-}"
INSTALL_ROOT="${SATDUMP_INSTALL_ROOT:-/opt/satdump}"
PROFILE="${SATDUMP_PROFILE:-headless}"
MODE="native"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 2)}"
UPDATE=1
INSTALL_DEPS=0
CLEAN=1
ALLOW_DIRTY=0

usage() {
    cat <<EOF
Использование: scripts/astra/build-satdump.sh [параметры]

  --source PATH          исходники SatDump (по умолчанию ${SOURCE})
  --install-root PATH    releases/current (по умолчанию ${INSTALL_ROOT})
  --branch NAME          обязательная ветка (по умолчанию ${BRANCH})
  --commit SHA           закрепить точный commit
  --profile headless|desktop|full
  --jobs N
  --install-deps         вызвать SatDump install-deps.sh
  --no-update            использовать имеющиеся исходники без fetch
  --no-clean             не очищать build-dir
  --allow-dirty          разрешить незакоммиченные изменения

Сборка выполняется обычным пользователем; sudo применяется только для APT,
versioned install prefix и переключения symlink current.
EOF
}

while (( $# )); do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --install-root) INSTALL_ROOT="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --commit) EXPECTED_COMMIT="$2"; shift 2 ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --install-deps) INSTALL_DEPS=1; shift ;;
        --no-update) UPDATE=0; shift ;;
        --no-clean) CLEAN=0; shift ;;
        --allow-dirty) ALLOW_DIRTY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

case "${PROFILE}" in headless|desktop|full) ;; *) die "Некорректный профиль: ${PROFILE}" ;; esac
[[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] || die "--jobs должен быть положительным числом"

ASTRA_VERSION="$(detect_astra_version)"
[[ "${ASTRA_VERSION}" == "1.6" || "${ASTRA_VERSION}" == "1.7" ]] \
    || die "Сборка разрешена только на Astra Linux 1.6/1.7"

BUILD_USER="$(real_user)"
BUILD_GROUP="$(real_group)"
if [[ ! -d "${SOURCE}/.git" ]]; then
    (( UPDATE == 1 )) || die "Исходники отсутствуют: ${SOURCE}"
    parent="$(dirname "${SOURCE}")"
    as_root install -d -m 0755 "${parent}"
    clone_root="$(mktemp -d "${TMPDIR:-/tmp}/satdump-clone.XXXXXX")"
    trap 'rm -rf "${clone_root}"' EXIT
    log_info "Клонирование SatDump ${BRANCH} во временный каталог"
    git clone --branch "${BRANCH}" --single-branch "${REPOSITORY}" "${clone_root}/SatDump"
    [[ ! -e "${SOURCE}" ]] || die "Целевой путь существует и не является Git-репозиторием: ${SOURCE}"
    as_root mv "${clone_root}/SatDump" "${SOURCE}"
    as_root chown -R "${BUILD_USER}:${BUILD_GROUP}" "${SOURCE}"
    rm -rf "${clone_root}"
    trap - EXIT
fi

[[ -d "${SOURCE}/.git" ]] || die "${SOURCE} не является Git-репозиторием"
if [[ ! -w "${SOURCE}/.git" ]]; then
    log_warn "Исходники SatDump недоступны пользователю ${BUILD_USER}; исправление владельца выделенного дерева"
    as_root chown -R "${BUILD_USER}:${BUILD_GROUP}" "${SOURCE}"
fi
if (( ALLOW_DIRTY == 0 )) && [[ -n "$(git -C "${SOURCE}" status --porcelain)" ]]; then
    die "Исходники SatDump содержат незакоммиченные изменения; используйте --allow-dirty осознанно"
fi

if (( UPDATE == 1 )); then
    log_info "Обновление ветки ${BRANCH}"
    git -C "${SOURCE}" fetch --prune origin "${BRANCH}"
    git -C "${SOURCE}" checkout "${BRANCH}"
    git -C "${SOURCE}" merge --ff-only "origin/${BRANCH}"
fi

ACTUAL_BRANCH="$(git -C "${SOURCE}" symbolic-ref --short HEAD 2>/dev/null || true)"
if [[ -n "${ACTUAL_BRANCH}" ]]; then
    [[ "${ACTUAL_BRANCH}" == "${BRANCH}" ]] || die "Ожидалась ветка ${BRANCH}, найдена ${ACTUAL_BRANCH}"
elif [[ -z "${EXPECTED_COMMIT}" ]]; then
    die "Исходники SatDump находятся в detached HEAD; задайте --commit или выполните update"
fi
if [[ -n "${EXPECTED_COMMIT}" ]]; then
    git -C "${SOURCE}" checkout --detach "${EXPECTED_COMMIT}"
fi
COMMIT="$(git -C "${SOURCE}" rev-parse HEAD)"
SHORT_COMMIT="${COMMIT:0:12}"
if [[ -n "${EXPECTED_COMMIT}" && "${COMMIT}" != "${EXPECTED_COMMIT}"* ]]; then
    die "Не удалось закрепить commit ${EXPECTED_COMMIT}; активен ${COMMIT}"
fi

chmod +x "${SOURCE}"/scripts/astra/*.sh 2>/dev/null || true
if (( INSTALL_DEPS == 1 )); then
    log_info "Установка зависимостей SatDump"
    bash "${SOURCE}/scripts/astra/install-deps.sh" \
        --profile "${PROFILE}" \
        --bootstrap-missing
fi
log_info "Проверка среды SatDump"
bash "${SOURCE}/scripts/astra/check-system.sh" --strict

RELEASE_NAME="satdump-1.2.2-${ASTRA_VERSION}-${PROFILE}-${SHORT_COMMIT}"
RELEASE_PREFIX="${INSTALL_ROOT}/releases/${RELEASE_NAME}"
BUILD_DIR="${SOURCE}/build/satprof-astra-${ASTRA_VERSION}-${PROFILE}-${SHORT_COMMIT}"
as_root install -d -o "${BUILD_USER}" -g "${BUILD_GROUP}" "${INSTALL_ROOT}/releases"

BUILD_ARGS=(
    --mode "${MODE}"
    --profile "${PROFILE}"
    --jobs "${JOBS}"
    --build-dir "${BUILD_DIR}"
    --prefix "${RELEASE_PREFIX}"
    --install
)
if (( CLEAN == 1 )); then
    BUILD_ARGS+=(--clean --clean-prefix)
fi

log_info "Сборка SatDump ${COMMIT} → ${RELEASE_PREFIX}"
bash "${SOURCE}/scripts/astra/build.sh" "${BUILD_ARGS[@]}"

log_info "Smoke-test установленного SatDump"
bash "${SOURCE}/scripts/astra/run.sh" --prefix "${RELEASE_PREFIX}" -- version
[[ -x "${RELEASE_PREFIX}/bin/satdump" ]] || die "После сборки отсутствует ${RELEASE_PREFIX}/bin/satdump"
[[ -d "${RELEASE_PREFIX}/share/satdump/pipelines" ]] || die "Не установлены pipelines"
[[ -d "${RELEASE_PREFIX}/share/satdump/resources" ]] || die "Не установлены resources"

cat >"/tmp/satdump-build-manifest.$$" <<EOF
schema=satprof-satdump-build/1
version=1.2.2
astra=${ASTRA_VERSION}
profile=${PROFILE}
mode=${MODE}
branch=${BRANCH}
commit=${COMMIT}
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source=${SOURCE}
EOF
as_root install -m 0644 "/tmp/satdump-build-manifest.$$" "${RELEASE_PREFIX}/.satprof-build-manifest"
rm -f "/tmp/satdump-build-manifest.$$"

PREVIOUS="$(readlink -f "${INSTALL_ROOT}/current" 2>/dev/null || true)"
atomic_symlink "${RELEASE_PREFIX}" "${INSTALL_ROOT}/current"
if ! bash "${SOURCE}/scripts/astra/run.sh" --prefix "${INSTALL_ROOT}/current" -- version; then
    if [[ -n "${PREVIOUS}" && -d "${PREVIOUS}" ]]; then
        log_error "Новая сборка не запускается; возврат на ${PREVIOUS}"
        atomic_symlink "${PREVIOUS}" "${INSTALL_ROOT}/current"
    fi
    exit 1
fi

# Keep a small number of versioned installations. Never remove current/previous.
mapfile -t releases < <(find "${INSTALL_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
keep="${SATDUMP_KEEP_RELEASES:-3}"
for ((index=keep; index<${#releases[@]}; index++)); do
    candidate="${releases[index]}"
    [[ "${candidate}" == "${RELEASE_PREFIX}" || "${candidate}" == "${PREVIOUS}" ]] && continue
    as_root rm -rf -- "${candidate}"
done

log_ok "SatDump готов: ${INSTALL_ROOT}/current"
printf 'commit=%s\nprefix=%s\n' "${COMMIT}" "${RELEASE_PREFIX}"
