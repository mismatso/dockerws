#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
command="${1:-help}"
networks() {
    local domain id network owner
    # Evitar process substitution: un inventario inválido debe hacer fallar el comando.
    local domains
    domains="$(python3 "$DOCKERWS_ROOT/scripts/registry.py" list)"
    while IFS= read -r domain; do
        [[ -n $domain ]] || continue
        id="$(site_field "$domain" id)"
        network="dockerws-edge-$id"
        if docker network inspect "$network" >/dev/null 2>&1; then
            owner="$(docker network inspect -f '{{ index .Labels "org.dockerws.site" }}' "$network")"
            [[ $owner == "$id" ]] || { echo "La red $network ya existe y no pertenece a este sitio." >&2; return 1; }
        else
            docker network create --label "org.dockerws.site=$id" "$network" >/dev/null
        fi
    done <<< "$domains"
}
case "$command" in
    networks) networks ;;
    proxy-up)
        require_root
        exec 9>/run/lock/dockerws-proxy.lock
        flock -w 120 9
        networks
        install -d -m 0755 /var/lib/dockerws/acme /etc/letsencrypt
        python3 "$DOCKERWS_ROOT/scripts/render-compose.py"
        python3 "$DOCKERWS_ROOT/scripts/render-proxy.py"
        proxy_compose config --quiet
        proxy_compose up -d --wait
        proxy_compose exec -T proxy nginx -t
        proxy_compose exec -T proxy nginx -s reload
        ;;
    site-up)
        require_root
        domain="${2:?Falta dominio}"
        kind="$(site_field "$domain" kind)"
        mode="$(site_field "$domain" database.mode)"
        app="$DOCKERWS_ROOT/sites/$domain/app"
        if [[ $mode == host && ! -f "$DOCKERWS_ROOT/sites/$domain/host-db.ready" ]]; then
            echo "Ejecutar primero scripts/provision-host-db.sh $domain" >&2; exit 1
        fi
        case "$kind" in
            laravel) [[ -f "$app/artisan" && -f "$app/vendor/autoload.php" ]] || { echo 'Importar Laravel y ejecutar composer install primero.' >&2; exit 1; } ;;
            wordpress) [[ -f "$app/wp-settings.php" && -f "$app/wp-config.php" ]] || { echo 'Importar WordPress conservando wp-config.php.' >&2; exit 1; } ;;
        esac
        networks
        site_compose "$domain" config --quiet
        site_compose "$domain" up -d --build --wait
        ;;
    db-up)
        domain="${2:?Falta dominio}"
        [[ $(site_field "$domain" database.mode) == container ]] || { echo 'No hay contenedor de BD en este sitio.' >&2; exit 1; }
        networks
        site_compose "$domain" up -d --wait db
        ;;
    site-stop) site_compose "${2:?Falta dominio}" stop ;;
    site) domain="${2:?Falta dominio}"; shift 2; site_compose "$domain" "$@" ;;
    proxy) shift; proxy_compose "$@" ;;
    list) python3 "$DOCKERWS_ROOT/scripts/registry.py" list ;;
    validate)
        python3 "$DOCKERWS_ROOT/scripts/render-compose.py"
        proxy_compose config --quiet
        domains="$(python3 "$DOCKERWS_ROOT/scripts/registry.py" list)"
        while IFS= read -r domain; do
            [[ -z $domain ]] || site_compose "$domain" config --quiet
        done <<< "$domains"
        ;;
    *) echo 'Uso: stack.sh list|networks|proxy-up|site-up DOMINIO|db-up DOMINIO|site-stop DOMINIO|site DOMINIO COMANDO...|proxy COMANDO...|validate' ;;
esac
