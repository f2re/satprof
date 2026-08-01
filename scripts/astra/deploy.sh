#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

SOURCE="${SATPROF_SOURCE:-${SATPROF_ROOT}}"
PREFIX="${SATPROF_PREFIX:-/opt/satprof}"
CONFIG="${SATPROF_CONFIG:-/etc/satprof/config.yaml}"
ENV_FILE="${SATPROF_ENV_FILE:-/etc/satprof/satprof.env}"
WORKSPACE="${SATPROF_WORKSPACE:-${PREFIX}/workspace}"
WHEELHOUSE=""
RELEASE_ID=""
SKIP_TESTS=0
NO_START=0
KEEP_RELEASES="${SATPROF_KEEP_RELEASES:-4}"
HEALTH_TIMEOUT="${SATPROF_HEALTH_TIMEOUT:-90}"

usage() {
    cat <<EOF
Использование: scripts/astra/deploy.sh [параметры]

  --source PATH       исходное дерево SatProf
  --prefix PATH       runtime root (по умолчанию ${PREFIX})
  --config PATH       постоянная конфигурация
  --workspace PATH    постоянное рабочее хранилище
  --wheelhouse PATH   офлайн-каталог Python wheels
  --release-id ID     имя релиза; иначе UTC+Git SHA
  --skip-tests        не выполнять pytest/compileall
  --no-start          установить, но не запускать службы
  --keep N            число хранимых релизов

Развёртывание создаёт неизменяемый release, новую venv, проверяет её, атомарно
переключает current/.venv и откатывается, если /health/ready не отвечает.
EOF
}

while (( $# )); do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --wheelhouse) WHEELHOUSE="$2"; shift 2 ;;
        --release-id) RELEASE_ID="$2"; shift 2 ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --no-start) NO_START=1; shift ;;
        --keep) KEEP_RELEASES="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

SOURCE="$(readlink -m "${SOURCE}")"
[[ -f "${SOURCE}/pyproject.toml" ]] || die "pyproject.toml не найден: ${SOURCE}"
[[ "${KEEP_RELEASES}" =~ ^[1-9][0-9]*$ ]] || die "--keep должен быть положительным числом"
PYTHON_BIN="$(find_python 2>/dev/null || true)"
[[ -n "${PYTHON_BIN}" ]] || die "Python 3.10+ не найден. Запустите scripts/astra/bootstrap-python.sh"

if [[ -z "${RELEASE_ID}" ]]; then
    commit="$(git -C "${SOURCE}" rev-parse --short=12 HEAD 2>/dev/null || printf source)"
    RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-${commit}"
fi
RELEASE_ID="$(safe_release_name "${RELEASE_ID}")"
RELEASES="${PREFIX}/.releases"
RELEASE="${RELEASES}/${RELEASE_ID}"
CURRENT_LINK="${PREFIX}/current"
VENV_LINK="${PREFIX}/.venv"
PREVIOUS_RELEASE="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"
PREVIOUS_VENV="$(readlink -f "${VENV_LINK}" 2>/dev/null || true)"

as_root install -d -m 0755 "${PREFIX}" "${RELEASES}" "$(dirname "${CONFIG}")"
as_root install -d -o satprof -g satprof -m 0750 "${WORKSPACE}"
[[ ! -e "${RELEASE}" ]] || die "Релиз уже существует: ${RELEASE}"
as_root install -d -m 0755 "${RELEASE}"

log_info "Копирование SatProf в ${RELEASE}"
as_root rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '.releases' \
    --exclude 'current' \
    --exclude 'workspace' \
    --exclude '__pycache__' \
    "${SOURCE}/" "${RELEASE}/"

log_info "Создание Python-окружения"
as_root "${PYTHON_BIN}" -m venv "${RELEASE}/.venv"
PIP=("${RELEASE}/.venv/bin/pip")
as_root "${PIP[@]}" install --upgrade pip setuptools wheel
INSTALL_ARGS=(install --no-build-isolation)
if [[ -n "${WHEELHOUSE}" ]]; then
    WHEELHOUSE="$(readlink -m "${WHEELHOUSE}")"
    [[ -d "${WHEELHOUSE}" ]] || die "Wheelhouse не найден: ${WHEELHOUSE}"
    INSTALL_ARGS+=(--no-index --find-links "${WHEELHOUSE}")
fi
if (( SKIP_TESTS == 0 )); then
    INSTALL_ARGS+=("${RELEASE}[all,test]")
else
    INSTALL_ARGS+=("${RELEASE}[all]")
fi
as_root "${PIP[@]}" "${INSTALL_ARGS[@]}"

if (( SKIP_TESTS == 0 )); then
    log_info "Проверка нового релиза"
    as_root env PYTHONPATH="${RELEASE}/src" "${RELEASE}/.venv/bin/python" -m compileall -q "${RELEASE}/src"
    if [[ -d "${RELEASE}/tests" ]]; then
        as_root env PYTHONPATH="${RELEASE}/src" "${RELEASE}/.venv/bin/pytest" -q "${RELEASE}/tests"
    fi
    if command_exists node && [[ -f "${RELEASE}/src/satprof_calibrator/web/static/app.js" ]]; then
        node --check "${RELEASE}/src/satprof_calibrator/web/static/app.js"
        find "${RELEASE}/src/satprof_calibrator/web/static" -maxdepth 1 -name '*.js' -print0 \
            | xargs -0 -r -n1 node --check
    fi
fi

if [[ ! -f "${CONFIG}" ]]; then
    log_info "Создание постоянной конфигурации ${CONFIG}"
    as_root install -m 0640 -o root -g satprof "${RELEASE}/config/config.example.yaml" "${CONFIG}"
    as_root sed -i "s|^workspace:.*|workspace: ${WORKSPACE}|" "${CONFIG}"
else
    log_info "Конфигурация сохранена без замены: ${CONFIG}"
fi

as_root install -m 0640 -o root -g satprof "${RELEASE}/systemd/satprof.env.example" "${ENV_FILE}.new"
as_root sed -i "s|SATPROF_CONFIG=.*|SATPROF_CONFIG=${CONFIG}|" "${ENV_FILE}.new"
if [[ -f "${ENV_FILE}" ]]; then
    as_root sh -c "grep -v '^SATPROF_CONFIG=' '${ENV_FILE}' >> '${ENV_FILE}.new' || true"
fi
as_root awk '!seen[$0]++' "${ENV_FILE}.new" | as_root tee "${ENV_FILE}" >/dev/null
as_root rm -f "${ENV_FILE}.new"
as_root chown root:satprof "${ENV_FILE}"
as_root chmod 0640 "${ENV_FILE}"

as_root "${RELEASE}/.venv/bin/satprof" init --config "${CONFIG}"

for unit in satprof-web.service satprof-worker.service satprof-wis2.service satprof-monitor.service satprof-monitor.timer; do
    [[ -f "${RELEASE}/systemd/${unit}" ]] || continue
    rendered="/tmp/${unit}.$$"
    sed "s|/opt/satprof|${PREFIX}|g" "${RELEASE}/systemd/${unit}" >"${rendered}"
    as_root install -m 0644 "${rendered}" "/etc/systemd/system/${unit}"
    rm -f "${rendered}"
done
as_root systemctl daemon-reload

cat >"/tmp/satprof-release-manifest.$$" <<EOF
schema=satprof-release/1
release_id=${RELEASE_ID}
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source=${SOURCE}
source_commit=$(git -C "${SOURCE}" rev-parse HEAD 2>/dev/null || printf unknown)
python=$(${RELEASE}/.venv/bin/python -V 2>&1)
astra=$(detect_astra_version)
workspace=${WORKSPACE}
config=${CONFIG}
EOF
as_root install -m 0644 "/tmp/satprof-release-manifest.$$" "${RELEASE}/RELEASE"
rm -f "/tmp/satprof-release-manifest.$$"

log_info "Атомарное переключение на ${RELEASE_ID}"
atomic_symlink "${RELEASE}" "${CURRENT_LINK}"
atomic_symlink "${RELEASE}/.venv" "${VENV_LINK}"

rollback() {
    log_error "Проверка нового релиза не пройдена; выполняется откат"
    if [[ -n "${PREVIOUS_RELEASE}" && -d "${PREVIOUS_RELEASE}" ]]; then
        atomic_symlink "${PREVIOUS_RELEASE}" "${CURRENT_LINK}"
    fi
    if [[ -n "${PREVIOUS_VENV}" && -d "${PREVIOUS_VENV}" ]]; then
        atomic_symlink "${PREVIOUS_VENV}" "${VENV_LINK}"
    fi
    as_root systemctl restart satprof-worker.service satprof-web.service 2>/dev/null || true
}

if (( NO_START == 0 )); then
    as_root systemctl enable satprof-worker.service satprof-web.service satprof-monitor.timer
    as_root systemctl restart satprof-worker.service satprof-web.service
    as_root systemctl start satprof-monitor.timer
    WIS2_ENABLED="$(${RELEASE}/.venv/bin/python - "${CONFIG}" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding='utf-8') as stream:
    cfg=yaml.safe_load(stream) or {}
print('1' if cfg.get('sources',{}).get('wis2',{}).get('enabled',False) else '0')
PY
)"
    if [[ "${WIS2_ENABLED}" == "1" ]]; then
        as_root systemctl enable --now satprof-wis2.service
    else
        as_root systemctl disable --now satprof-wis2.service 2>/dev/null || true
    fi
    PORT="$(${RELEASE}/.venv/bin/python - "${CONFIG}" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding='utf-8') as stream:
    cfg=yaml.safe_load(stream) or {}
print(int(cfg.get('web',{}).get('port',8088)))
PY
)"
    if ! wait_http "http://127.0.0.1:${PORT}/health/ready" "${HEALTH_TIMEOUT}"; then
        rollback
        exit 1
    fi
    log_ok "Readiness check пройден"
fi

mapfile -t releases < <(find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
for ((index=KEEP_RELEASES; index<${#releases[@]}; index++)); do
    candidate="${releases[index]}"
    [[ "${candidate}" == "${RELEASE}" || "${candidate}" == "${PREVIOUS_RELEASE}" ]] && continue
    as_root rm -rf -- "${candidate}"
done

log_ok "SatProf развёрнут: ${RELEASE_ID}"
printf 'release=%s\ncurrent=%s\nworkspace=%s\nconfig=%s\n' "${RELEASE}" "${CURRENT_LINK}" "${WORKSPACE}" "${CONFIG}"
