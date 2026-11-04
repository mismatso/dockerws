#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for script in scripts/*.sh host/*.sh tests/*.sh; do bash -n "$script"; done
if command -v shellcheck >/dev/null; then
    shellcheck -S warning -x scripts/*.sh host/*.sh tests/*.sh
else
    echo 'shellcheck no está instalado; se omite el análisis estático de Bash.'
fi
python3 -m unittest discover -s tests -p 'test_*.py' -v
