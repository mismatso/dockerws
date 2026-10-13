#!/usr/bin/env bash
# Biblioteca compartida; ejecutar los scripts con Bash, nunca con sh.
set -Eeuo pipefail
DOCKERWS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export DOCKERWS_ROOT

site_field() {
    python3 "$DOCKERWS_ROOT/scripts/registry.py" field "$1" "$2"
}
site_compose() {
    local domain="$1"
    shift
    site_field "$domain" id >/dev/null
    docker compose --project-directory "$DOCKERWS_ROOT/sites/$domain" \
        --env-file "$DOCKERWS_ROOT/sites/$domain/.env" \
        -f "$DOCKERWS_ROOT/sites/$domain/compose.yaml" "$@"
}
proxy_compose() {
    docker compose --project-directory "$DOCKERWS_ROOT/proxy" \
        -f "$DOCKERWS_ROOT/proxy/compose.generated.yaml" "$@"
}
require_root() {
    if (( EUID != 0 )); then
        echo 'Este script debe ejecutarse como root en el servidor.' >&2
        exit 1
    fi
}
