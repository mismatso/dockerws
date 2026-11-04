#!/usr/bin/env bash
# Despliegue de sitios Laravel y PHP, en sitio o con releases atómicos.
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
require_root
[[ ${1:-} && ${1:-} != -* ]] || {
    cat >&2 <<'EOF'
Uso: deploy.sh DOMINIO [--from DIR | --ref REF] [--skip-backup] [--no-migrate] [--no-composer] [--assets]
                       [--no-maintenance] [--service web] [--verbose]
     deploy.sh DOMINIO --rollback [RELEASE] [--force]
     deploy.sh DOMINIO --init-releases | --revert-releases
EOF
    exit 1
}
exec python3 "$DOCKERWS_ROOT/scripts/deploy.py" deploy "$@"
