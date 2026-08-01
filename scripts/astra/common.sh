#!/usr/bin/env bash
set -Eeuo pipefail

SATPROF_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SATPROF_ROOT="$(cd "${SATPROF_SCRIPT_DIR}/../.." && pwd)"

log_info() { printf '\033[1;34mℹ %s\033[0m\n' "$*"; }
log_ok() { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
log_warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$*" >&2; }
log_error() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
die() { log_error "$*"; exit 1; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

as_root() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    elif command_exists sudo; then
        sudo "$@"
    else
        die "Для команды требуются права root: $*"
    fi
}

real_user() {
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        printf '%s\n' "${SUDO_USER}"
    else
        id -un
    fi
}

real_group() {
    id -gn "$(real_user)"
}

detect_astra_version() {
    local value=""
    if [[ -r /etc/astra/build_version ]]; then
        value="$(tr -d '\r\n' </etc/astra/build_version)"
    elif [[ -r /etc/os-release ]]; then
        value="$(. /etc/os-release; printf '%s' "${VERSION_ID:-}")"
    fi
    case "${value}" in
        *1.6*) printf '1.6\n' ;;
        *1.7*) printf '1.7\n' ;;
        *) printf 'unknown\n' ;;
    esac
}

find_python() {
    local candidate
    for candidate in \
        "${PYTHON:-}" \
        /opt/satprof/toolchain/python/bin/python3.11 \
        /opt/satprof/toolchain/python/bin/python3 \
        /usr/local/bin/python3.11 \
        /usr/bin/python3.11 \
        /usr/local/bin/python3.10 \
        /usr/bin/python3.10 \
        "$(command -v python3 2>/dev/null || true)"; do
        [[ -n "${candidate}" && -x "${candidate}" ]] || continue
        if "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
        then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

atomic_symlink() {
    local target="$1" link="$2" temporary="${link}.new.$$"
    as_root ln -sfn "${target}" "${temporary}"
    as_root mv -Tf "${temporary}" "${link}"
}

wait_http() {
    local url="$1" timeout="${2:-60}" elapsed=0 code
    while (( elapsed < timeout )); do
        if command_exists curl; then
            code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "${url}" 2>/dev/null || true)"
            [[ "${code}" =~ ^2 ]] && return 0
        else
            python3 - "${url}" <<'PY' >/dev/null 2>&1 && return 0 || true
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
    raise SystemExit(0 if 200 <= response.status < 300 else 1)
PY
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

safe_release_name() {
    printf '%s' "$1" | tr -cs 'A-Za-z0-9._-' '_'
}
