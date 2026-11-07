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
# Red frontal del sitio (dockerws-edge-ID), compartida con el proxy. Compose la declara external:
# sin ella no arranca ni un "run" del sitio. Se crea si falta; si existe, debe ser de este sitio.
site_network() {
    local id network owner
    id="$(site_field "$1" id)"
    network="dockerws-edge-$id"
    if docker network inspect "$network" >/dev/null 2>&1; then
        owner="$(docker network inspect -f '{{ index .Labels "org.dockerws.site" }}' "$network")"
        [[ $owner == "$id" ]] || { echo "La red $network ya existe y no pertenece a este sitio." >&2; return 1; }
    else
        docker network create --label "org.dockerws.site=$id" "$network" >/dev/null
    fi
}
proxy_compose() {
    docker compose --project-directory "$DOCKERWS_ROOT/proxy" \
        -f "$DOCKERWS_ROOT/proxy/compose.generated.yaml" "$@"
}
# Proyecto del proxy en instalaciones anteriores (contenedor dockerws-proxy-proxy-1).
LEGACY_PROXY_PROJECT=dockerws-proxy
# shellcheck disable=SC2120  # stack.sh pasa -a para incluir el contenedor detenido.
legacy_proxy() {
    docker ps -q "$@" --filter "label=com.docker.compose.project=$LEGACY_PROXY_PROJECT" \
        --filter label=com.docker.compose.service=proxy
}
# Contenedor del proxy en marcha: el actual o, mientras no se reemplace con proxy-up --recreate, el antiguo.
proxy_container() {
    local id
    id="$(proxy_compose ps -q proxy 2>/dev/null || true)"
    [[ -n $id ]] || id="$(legacy_proxy)"
    printf '%s\n' "$id"
}
proxy_exec() {
    local container
    container="$(proxy_container)"
    [[ -n $container ]] || { echo 'El proxy no está en marcha; ejecuta stack.sh proxy-up.' >&2; return 1; }
    docker exec "$container" "$@"
}
require_root() {
    if (( EUID != 0 )); then
        echo 'Este script debe ejecutarse como root en el servidor.' >&2
        exit 1
    fi
}
