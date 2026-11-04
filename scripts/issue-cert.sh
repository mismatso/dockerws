#!/usr/bin/env bash
# Uso: issue-cert.sh DOMINIO [CORREO] [--dry-run | --check]
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
require_root
usage='Uso: issue-cert.sh DOMINIO [CORREO] [--dry-run | --check]'
domain="${1:?$usage}"
shift
email=''
mode=issue
for arg in "$@"; do
    case "$arg" in
        --dry-run|--check)
            [[ $mode == issue ]] || { echo "$usage" >&2; exit 1; }
            mode="${arg#--}"
            ;;
        *@*)
            [[ -z $email ]] || { echo "$usage" >&2; exit 1; }
            email="$arg"
            ;;
        *) echo "Argumento desconocido: $arg. $usage" >&2; exit 1 ;;
    esac
done
site_field "$domain" id >/dev/null
if [[ $mode == check ]]; then
    # Verificación local de HTTP-01: no contacta a Let's Encrypt ni depende de staging.
    exec python3 "$DOCKERWS_ROOT/scripts/acme_check.py" "$domain"
fi
[[ -x /etc/letsencrypt/renewal-hooks/deploy/50-dockerws ]] || {
    echo 'Ejecutar primero host/install-automation.sh.' >&2
    exit 1
}
args=(certonly --webroot -w /var/lib/dockerws/acme --cert-name "$domain"
      --non-interactive --agree-tos -d "$domain")
# Let's Encrypt ya no envía avisos de vencimiento; con una cuenta existente el correo no se usa.
if [[ -n $email ]]; then
    args+=(--email "$email")
else
    args+=(--register-unsafely-without-email)
fi
read -r -a aliases <<< "$(site_field "$domain" aliases)"
for alias in "${aliases[@]}"; do args+=(-d "$alias"); done
[[ $mode != dry-run ]] || args+=(--dry-run)
certbot "${args[@]}"
if [[ $mode == issue ]]; then
    "$DOCKERWS_ROOT/scripts/reload-proxy.sh"
fi
