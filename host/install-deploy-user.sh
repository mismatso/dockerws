#!/usr/bin/env bash
# Prepara un usuario de despliegue sin sudo amplio: carpeta de subida por sitio y sudo limitado a deploy.sh.
# Uso: install-deploy-user.sh USUARIO DOMINIO [DOMINIO...]
source "$(dirname -- "${BASH_SOURCE[0]}")/../scripts/lib.sh"
require_root
user="${1:?Uso: install-deploy-user.sh USUARIO DOMINIO [DOMINIO...]}"
shift
(( $# )) || { echo 'Indica al menos un dominio.' >&2; exit 1; }
[[ $user =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo 'Nombre de usuario inválido.' >&2; exit 1; }
id -u "$user" >/dev/null 2>&1 || { echo "No existe el usuario $user; créalo antes (adduser --disabled-password $user)." >&2; exit 1; }
[[ $(id -u "$user") != 0 ]] || { echo 'El usuario de despliegue no puede ser root.' >&2; exit 1; }
# La ruta se incrusta en sudoers: limitar su sintaxis.
[[ $DOCKERWS_ROOT =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'La ruta del proyecto no admite espacios ni metacaracteres.' >&2; exit 1; }
# sudoers solo es seguro si nadie más puede modificar lo que ejecuta.
for path in "$DOCKERWS_ROOT" "$DOCKERWS_ROOT/scripts" "$DOCKERWS_ROOT/scripts/deploy.sh" "$DOCKERWS_ROOT/scripts/deploy.py"; do
    read -r owner mode < <(stat -c '%u %a' "$path")
    if [[ $owner != 0 ]] || (( 8#$mode & 8#022 )); then
        echo "$path debe pertenecer a root y no ser escribible por grupo u otros." >&2; exit 1
    fi
done
incoming="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from registry import settings; print(settings()["deploy_incoming_dir"])' "$DOCKERWS_ROOT/scripts")"
[[ $incoming =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'deploy_incoming_dir inválido.' >&2; exit 1; }
install -d -o root -g root -m 0755 "$incoming"
rules=()
for domain in "$@"; do
    site_field "$domain" id >/dev/null
    kind="$(site_field "$domain" kind)"
    [[ $kind == laravel || $kind == php ]] || { echo "$domain es $kind; deploy.sh admite laravel y php." >&2; exit 1; }
    install -d -o "$user" -g "$user" -m 0750 "$incoming/$domain"
    rules+=("$DOCKERWS_ROOT/scripts/deploy.sh $domain" "$DOCKERWS_ROOT/scripts/deploy.sh $domain *")
done
file="/etc/sudoers.d/dockerws-deploy-$user"
temp="$(mktemp)"
trap 'rm -f -- "$temp"' EXIT
{
    echo "# Generado por DockerWS: $user solo puede ejecutar deploy.sh para estos sitios."
    printf '%s ALL=(root) NOPASSWD: ' "$user"
    (IFS=,; echo "${rules[*]}" | sed 's/,/, /g')
} > "$temp"
visudo -cqf "$temp" || { echo 'La regla generada no es válida; no se instaló.' >&2; exit 1; }
install -o root -g root -m 0440 "$temp" "$file"
echo "Instalado $file."
echo "Subida:      rsync -az --delete ./ $user@SERVIDOR:$incoming/DOMINIO/"
echo "Despliegue:  ssh $user@SERVIDOR sudo $DOCKERWS_ROOT/scripts/deploy.sh DOMINIO"
