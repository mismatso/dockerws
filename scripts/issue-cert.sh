#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
require_root
domain="${1:?Uso: issue-cert.sh dominio correo [--dry-run]}"
email="${2:?Falta correo para avisos de certificados}"
site_field "$domain" id >/dev/null
[[ -x /etc/letsencrypt/renewal-hooks/deploy/50-dockerws ]] || {
    echo 'Ejecutar primero host/install-automation.sh.' >&2
    exit 1
}
args=(certonly --webroot -w /var/lib/dockerws/acme --cert-name "$domain"
      --non-interactive --agree-tos --email "$email" -d "$domain")
read -r -a aliases <<< "$(site_field "$domain" aliases)"
for alias in "${aliases[@]}"; do args+=(-d "$alias"); done
if [[ ${3:-} == --dry-run ]]; then
    args+=(--dry-run)
elif [[ $# -gt 2 ]]; then
    echo 'Solo se acepta --dry-run como tercer argumento.' >&2
    exit 1
fi
certbot "${args[@]}"
if [[ ${3:-} != --dry-run ]]; then
    "$DOCKERWS_ROOT/scripts/reload-proxy.sh"
fi
