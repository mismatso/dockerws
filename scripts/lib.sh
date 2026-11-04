#!/usr/bin/env bash
# Biblioteca compartida; ejecutar los scripts con Bash, nunca con sh.
set -Eeuo pipefail
DOCKERWS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export DOCKERWS_ROOT

site_field() {
    python3 "$DOCKERWS_ROOT/scripts/registry.py" field "$1" "$2"
}
# Archivos Compose del sitio: compose.yaml y, en orden, los overlays de compose.d/.
# Los overlays permiten agregar servicios o montajes sin reescribir compose.yaml.
site_compose_files() {
    local dir="$DOCKERWS_ROOT/sites/$1"
    local -a overlays=()
    printf '%s\n' "$dir/compose.yaml"
    shopt -s nullglob
    overlays=("$dir"/compose.d/*.yaml)
    shopt -u nullglob
    # El glob de Bash ordena según la configuración regional; forzar orden bytewise.
    if (( ${#overlays[@]} )); then
        printf '%s\n' "${overlays[@]}" | LC_ALL=C sort
    fi
}
site_compose() {
    local domain="$1" file
    local -a files=()
    shift
    site_field "$domain" id >/dev/null
    while IFS= read -r file; do files+=(-f "$file"); done < <(site_compose_files "$domain")
    docker compose --project-directory "$DOCKERWS_ROOT/sites/$domain" \
        --env-file "$DOCKERWS_ROOT/sites/$domain/.env" "${files[@]}" "$@"
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
