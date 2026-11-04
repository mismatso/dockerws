#!/usr/bin/env bash
# Despliegue de sitios Laravel y PHP. Uso: deploy.sh DOMINIO [--from DIR | --ref REF] [opciones]
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
require_root
[[ ${1:-} && ${1:-} != -* ]] || {
    echo 'Uso: deploy.sh DOMINIO [--from DIR | --ref REF] [--skip-backup] [--no-migrate] [--no-composer] [--service web] [--verbose]' >&2
    exit 1
}
exec python3 "$DOCKERWS_ROOT/scripts/deploy.py" deploy "$@"
