#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
# Con argumentos, permite automatizar usando exactamente la misma validación.
if (( $# )); then exec python3 "$DOCKERWS_ROOT/scripts/scaffold.py" "$@"; fi
require_root
choose() {
    local prompt="$1" answer
    shift
    printf '\n%s\n' "$prompt" >&2
    select answer in "$@"; do
        if [[ -n $answer ]]; then printf '%s' "$answer"; return; fi
        echo 'Selecciona un número de la lista.' >&2
    done
    return 1
}
read -rp 'Dominio principal (sin https://): ' domain
read -rp 'Alias separados por espacios (opcional): ' alias_line
kind="$(choose 'Tipo de sitio:' wordpress laravel php static)"
args=(--domain "$domain" --type "$kind")
read -ra aliases <<< "$alias_line"
for alias in "${aliases[@]}"; do args+=(--alias "$alias"); done
if [[ $kind != static ]]; then
    read -rp 'Versión PHP [8.4]: ' php
    args+=(--php "${php:-8.4}")
fi
engine=none
mode=none
if [[ $kind == wordpress ]]; then
    engine="$(choose 'WordPress requiere base de datos. Motor:' mariadb mysql)"
elif [[ $kind != static ]]; then
    required="$(choose '¿Requiere base de datos?' si no)"
    if [[ $required == si ]]; then engine="$(choose 'Motor:' mariadb mysql postgres sqlite)"; fi
fi
if [[ $engine == sqlite ]]; then
    mode=file
elif [[ $engine != none ]]; then
    mode="$(choose 'Modalidad: container = instancia dedicada; host = instancia compartida de Debian:' container host)"
fi
args+=(--database "$engine" --db-mode "$mode")
if [[ $mode == host ]]; then
    default_socket=/run/mysqld
    [[ $engine != postgres ]] || default_socket=/var/run/postgresql
    read -rp "Directorio del socket [$default_socket]: " socket_dir
    args+=(--socket-dir "${socket_dir:-$default_socket}")
    if [[ $engine == postgres ]]; then
        read -rp 'Puerto del clúster PostgreSQL [5432]: ' port
        args+=(--db-port "${port:-5432}")
    else
        read -rp 'Archivo del socket [mysqld.sock]: ' socket_name
        args+=(--socket-name "${socket_name:-mysqld.sock}")
    fi
fi
printf '\nSe creará %s: %s, %s/%s.\n' "$domain" "$kind" "$engine" "$mode"
read -rp 'Crear estructura [S/n]: ' confirm
[[ ${confirm,,} != n ]] || exit 0
python3 "$DOCKERWS_ROOT/scripts/scaffold.py" "${args[@]}"
if [[ $mode == host ]]; then
    read -rp 'Crear ahora la base y el usuario en el host [S/n]: ' confirm
    if [[ ${confirm,,} != n ]]; then
        admin_args=()
        if [[ $engine != postgres ]]; then
            read -rp 'Archivo .cnf de administrador (vacío = root por socket): ' admin_cnf
            [[ -z $admin_cnf ]] || admin_args+=(--admin-cnf "$admin_cnf")
        fi
        "$DOCKERWS_ROOT/scripts/provision-host-db.sh" "$domain" "${admin_args[@]}"
    fi
fi
