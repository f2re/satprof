#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

UPDATE_SATDUMP=0
while (( $# )); do
    case "$1" in
        --with-satdump) UPDATE_SATDUMP=1; shift ;;
        -h|--help)
            printf 'Использование: scripts/update.sh [--with-satdump]\n'
            exit 0
            ;;
        *) printf 'Неизвестный параметр: %s\n' "$1" >&2; exit 2 ;;
    esac
done

cd "${ROOT}"
if [[ -d .git ]]; then
    git fetch origin main
    git merge --ff-only origin/main
fi
if (( UPDATE_SATDUMP == 1 )); then
    bash scripts/astra/build-satdump.sh
fi
exec bash scripts/astra/deploy.sh --source "${ROOT}"
