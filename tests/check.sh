#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for script in scripts/*.sh host/*.sh tests/*.sh; do bash -n "$script"; done
python3 -m unittest discover -s tests -p 'test_*.py' -v
