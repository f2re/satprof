#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

STRICT=0
JSON=0
SATDUMP_SOURCE="${SATDUMP_SOURCE:-/opt/SatDump}"
SATDUMP_PREFIX="${SATDUMP_PREFIX:-/opt/satdump/current}"
WORKSPACE="${SATPROF_WORKSPACE:-/opt/satprof/workspace}"

usage() {
    cat <<'EOF'
Использование: scripts/astra/check-system.sh [--strict] [--json]

Проверяет Astra Linux, Python, ecCodes, systemd, место на диске и установленный
SatDump release/1.2.2. Без --strict диагностические проблемы не меняют код 0.
EOF
}

while (( $# )); do
    case "$1" in
        --strict) STRICT=1; shift ;;
        --json) JSON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный параметр: $1" ;;
    esac
done

failures=0
warnings=0
REPORT=()
record() {
    local state="$1" name="$2" message="$3"
    REPORT+=("${state}|${name}|${message}")
    case "${state}" in
        ok) (( JSON == 1 )) || log_ok "${name}: ${message}" ;;
        warning) warnings=$((warnings + 1)); (( JSON == 1 )) || log_warn "${name}: ${message}" ;;
        error) failures=$((failures + 1)); (( JSON == 1 )) || log_error "${name}: ${message}" ;;
    esac
}

ASTRA_VERSION="$(detect_astra_version)"
case "${ASTRA_VERSION}" in
    1.6|1.7) record ok astra "Astra Linux ${ASTRA_VERSION}" ;;
    unknown) record error astra "версия не определена; поддерживаются 1.6 и 1.7" ;;
    *) record error astra "неподдерживаемая версия ${ASTRA_VERSION}" ;;
esac

case "$(uname -m)" in
    x86_64|amd64) record ok architecture "$(uname -m)" ;;
    aarch64|arm64) record warning architecture "ARM64 не входит в основной аттестованный профиль" ;;
    *) record error architecture "непроверенная архитектура $(uname -m)" ;;
esac

if PYTHON_BIN="$(find_python 2>/dev/null)"; then
    PYTHON_VERSION="$(${PYTHON_BIN} -c 'import sys; print(".".join(map(str,sys.version_info[:3])))')"
    record ok python "${PYTHON_VERSION} (${PYTHON_BIN})"
else
    record error python "Python 3.10+ не найден; выполните bootstrap-python.sh"
fi

for command in git rsync tar sha256sum systemctl curl pkg-config; do
    if command_exists "${command}"; then
        record ok "command.${command}" "$(command -v "${command}")"
    else
        case "${command}" in
            curl) record warning "command.${command}" "не найден; возможна работа через urllib" ;;
            *) record error "command.${command}" "не найден" ;;
        esac
    fi
done

if command_exists dpkg-query && dpkg-query -W -f='${Status}' libeccodes0 2>/dev/null | grep -q 'install ok installed'; then
    record ok eccodes "системная библиотека установлена"
elif ldconfig -p 2>/dev/null | grep -q 'libeccodes'; then
    record ok eccodes "библиотека найдена через ldconfig"
else
    record error eccodes "libeccodes не установлена"
fi

mkdir -p "${WORKSPACE}" 2>/dev/null || true
if [[ -d "${WORKSPACE}" && -w "${WORKSPACE}" ]]; then
    free_kb="$(df -Pk "${WORKSPACE}" | awk 'NR==2 {print $4}')"
    if [[ "${free_kb}" =~ ^[0-9]+$ ]] && (( free_kb >= 5 * 1024 * 1024 )); then
        record ok workspace "доступен для записи, свободно $((free_kb / 1024 / 1024)) ГиБ"
    else
        record warning workspace "доступен, но свободно менее 5 ГиБ"
    fi
else
    record error workspace "нет каталога или прав записи: ${WORKSPACE}"
fi

if [[ -d "${SATDUMP_SOURCE}" ]]; then
    branch="$(git -C "${SATDUMP_SOURCE}" symbolic-ref --short HEAD 2>/dev/null || true)"
    if [[ "${branch}" == "release/1.2.2" ]]; then
        record ok satdump.source "ветка release/1.2.2"
    elif [[ -n "${branch}" ]]; then
        record error satdump.source "ожидалась release/1.2.2, найдена ${branch}"
    else
        record warning satdump.source "ветка не определена"
    fi
    if [[ -x "${SATDUMP_SOURCE}/scripts/astra/check-system.sh" || -f "${SATDUMP_SOURCE}/scripts/astra/check-system.sh" ]]; then
        record ok satdump.scripts "Astra-сценарии присутствуют"
    else
        record error satdump.scripts "scripts/astra/check-system.sh отсутствует"
    fi
else
    record error satdump.source "исходники не найдены: ${SATDUMP_SOURCE}"
fi

for path in \
    "${SATDUMP_PREFIX}/bin/satdump" \
    "${SATDUMP_PREFIX}/share/satdump/resources" \
    "${SATDUMP_PREFIX}/share/satdump/pipelines" \
    "${SATDUMP_PREFIX}/share/satdump/satdump_cfg.json"; do
    if [[ -e "${path}" ]]; then
        record ok satdump.runtime "${path}"
    else
        record error satdump.runtime "не найден ${path}"
    fi
done

if [[ -f "${SATDUMP_PREFIX}/.satdump-install-root" ]]; then
    record ok satdump.marker "версионированная установка подтверждена"
else
    record warning satdump.marker "нет .satdump-install-root"
fi

if (( JSON == 1 )); then
    printf '{"astra_version":"%s","failures":%d,"warnings":%d,"checks":[' "${ASTRA_VERSION}" "${failures}" "${warnings}"
    first=1
    for entry in "${REPORT[@]}"; do
        IFS='|' read -r state name message <<<"${entry}"
        (( first == 1 )) || printf ','
        first=0
        python3 - "${state}" "${name}" "${message}" <<'PY'
import json, sys
print(json.dumps({"status":sys.argv[1],"name":sys.argv[2],"message":sys.argv[3]},ensure_ascii=False),end="")
PY
    done
    printf ']}\n'
else
    printf '\n'
    if (( failures == 0 )); then
        log_ok "Среда готова. Предупреждений: ${warnings}."
    else
        log_error "Критических проблем: ${failures}; предупреждений: ${warnings}."
    fi
fi

if (( STRICT == 1 && failures > 0 )); then
    exit 1
fi
exit 0
