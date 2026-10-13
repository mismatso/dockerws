#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
require_root
# Serializa emisiones, renovaciones y recargas manuales.
exec 9>"${PROXY_LOCK_FILE:-/run/lock/dockerws-proxy.lock}"
flock -w 120 9
snapshot="$(mktemp -d /tmp/dockerws-proxy-config.XXXXXX)"
cp -a "$DOCKERWS_ROOT/proxy/conf.d/." "$snapshot/"
finish() {
    local status=$?
    if (( status != 0 )); then
        # Restituye la configuración en disco; Nginx conserva la ya cargada.
        find "$DOCKERWS_ROOT/proxy/conf.d" -maxdepth 1 -type f -name '*.conf' -delete
        cp -a "$snapshot/." "$DOCKERWS_ROOT/proxy/conf.d/"
        echo 'La recarga falló; se restauró la configuración anterior.' >&2
    fi
    rm -rf -- "$snapshot"
    exit "$status"
}
trap finish EXIT
python3 "$DOCKERWS_ROOT/scripts/render-proxy.py"
proxy_compose exec -T proxy nginx -t
proxy_compose exec -T proxy nginx -s reload
