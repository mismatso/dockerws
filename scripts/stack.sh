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
        recreate=no
        case "${2:-}" in
            '') ;;
            --recreate) recreate=yes ;;
            *) echo 'Uso: stack.sh proxy-up [--recreate]' >&2; exit 1 ;;
        esac
        exec 9>/run/lock/dockerws-proxy.lock
        flock -w 120 9
        networks
        install -d -m 0755 /var/lib/dockerws/acme /etc/letsencrypt
        python3 "$DOCKERWS_ROOT/scripts/render-compose.py"
        python3 "$DOCKERWS_ROOT/scripts/render-proxy.py"
        proxy_compose config --quiet
        container="$(proxy_compose ps -q proxy)"
        if [[ $recreate == no && -n $container && $(docker inspect -f '{{.State.Running}}' "$container") == true ]]; then
            # Proxy en ejecución: conectar las redes nuevas sin recrearlo, para no cortar a los demás sitios.
            # docker network connect es persistente: sobrevive a reinicios del contenedor y del host.
            current="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' "$container")"
            actions="$(python3 "$DOCKERWS_ROOT/scripts/proxy_networks.py" <<< "$current")"
            while read -r action network; do
                [[ -n $action ]] || continue
                echo "Proxy: $action $network"
                docker network "$action" "$network" "$container"
            done <<< "$actions"
        else
            proxy_compose up -d --wait
        fi
        proxy_compose exec -T proxy nginx -t
        proxy_compose exec -T proxy nginx -s reload
        ;;
    site-up)
        require_root
        domain="${2:?Falta dominio}"
        build_args=()
        [[ ${3:-} != --verbose ]] || build_args+=(--verbose)
        kind="$(site_field "$domain" kind)"
        mode="$(site_field "$domain" database.mode)"
        app="$DOCKERWS_ROOT/sites/$domain/app"
        if [[ $mode == host && ! -f "$DOCKERWS_ROOT/sites/$domain/host-db.ready" ]]; then
            echo "Ejecutar primero scripts/provision-host-db.sh $domain" >&2; exit 1
        fi
        case "$kind" in
            laravel) [[ -f "$app/artisan" && -f "$app/vendor/autoload.php" ]] || { echo "Importar Laravel y ejecutar primero: stack.sh site-composer $domain (o deploy.sh $domain --from DIR)." >&2; exit 1; } ;;
            wordpress) [[ -f "$app/wp-settings.php" && -f "$app/wp-config.php" ]] || { echo 'Importar WordPress conservando wp-config.php.' >&2; exit 1; } ;;
        esac
        networks
        site_compose "$domain" config --quiet
        # La salida del build solo se muestra si falla (o con --verbose).
        python3 "$DOCKERWS_ROOT/scripts/deploy.py" build "$domain" "${build_args[@]}"
        site_compose "$domain" up -d --no-build --quiet-pull --wait
        ;;
    db-import)
        require_root
        shift
        exec python3 "$DOCKERWS_ROOT/scripts/backups.py" import "$@"
        ;;
    db-up)
        domain="${2:?Falta dominio}"
        [[ $(site_field "$domain" database.mode) == container ]] || { echo 'No hay contenedor de BD en este sitio.' >&2; exit 1; }
        networks
        site_compose "$domain" up -d --wait db
        ;;
    site-composer)
        require_root
        shift
        exec python3 "$DOCKERWS_ROOT/scripts/deploy.py" composer "$@"
        ;;
    scheduler-enable)
        require_root
        domain="${2:?Falta dominio}"
        shift 2
        cron_file=''
        enable_args=()
        while (( $# )); do
            case "$1" in
                --cron-file) cron_file="${2:?Falta archivo}"; enable_args+=("$1" "$2"); shift 2 ;;
                --memory|--service) enable_args+=("$1" "${2:?Falta valor de $1}"); shift 2 ;;
                *) echo "Opción desconocida: $1" >&2; exit 1 ;;
            esac
        done
        python3 "$DOCKERWS_ROOT/scripts/workers.py" enable "$domain" scheduler "${enable_args[@]}"
        trap 'echo "El overlay quedó creado; revisa el error o deshaz con: stack.sh scheduler-disable $domain" >&2' ERR
        networks
        site_compose "$domain" config --quiet
        python3 "$DOCKERWS_ROOT/scripts/deploy.py" build "$domain"
        if [[ -n $cron_file ]]; then
            # Crear el contenedor antes del traspaso: en la ventana solo queda arrancarlo.
            site_compose "$domain" up -d --no-build --no-start scheduler
            python3 "$DOCKERWS_ROOT/scripts/workers.py" cron-handoff "$domain" "$cron_file"
        else
            site_compose "$domain" up -d --no-build --wait scheduler
        fi
        trap - ERR
        site_compose "$domain" ps scheduler
        ;;
    scheduler-disable)
        require_root
        domain="${2:?Falta dominio}"
        [[ -f "$DOCKERWS_ROOT/sites/$domain/compose.d/50-scheduler.yaml" ]] || {
            echo 'Este sitio no tiene compose.d/50-scheduler.yaml; si el scheduler está en compose.yaml, edítalo a mano.' >&2; exit 1; }
        site_compose "$domain" rm --stop --force scheduler
        python3 "$DOCKERWS_ROOT/scripts/workers.py" disable "$domain" scheduler
        echo 'Si la aplicación necesita el scheduler, vuelve a habilitarlo o restituye su cron.'
        ;;
    env-split)
        require_root
        shift
        exec python3 "$DOCKERWS_ROOT/scripts/envfiles.py" split "$@"
        ;;
    env-reload)
        require_root
        domain="${2:?Falta dominio}"
        service="web"
        [[ ${3:-} != --service ]] || service="${4:?Falta servicio}"
        python3 "$DOCKERWS_ROOT/scripts/envfiles.py" check "$domain"
        site_compose "$domain" config --quiet
        # Compose recrea los servicios cuyo entorno cambió (runtime.env o app.env).
        site_compose "$domain" up -d --no-build --wait
        if [[ $(site_field "$domain" kind) == laravel ]]; then
            site_compose "$domain" exec -T --user 33:33 "$service" php artisan config:cache
        fi
        ;;
    status) python3 "$DOCKERWS_ROOT/scripts/workers.py" status ${2:+"$2"} ;;
    site-stop) site_compose "${2:?Falta dominio}" stop ;;
    site-rename)
        require_root
        shift
        exec python3 "$DOCKERWS_ROOT/scripts/rename.py" "$@"
        ;;
    site)
        domain="${2:?Falta dominio}"
        shift 2
        # El sistema de archivos de los contenedores es de solo lectura y HOME (/var/www) no se puede escribir:
        # herramientas como artisan tinker (psysh) necesitan un HOME escribible. /tmp es tmpfs.
        if [[ ${1:-} == exec || ${1:-} == run ]]; then
            set -- "$1" -e HOME=/tmp "${@:2}"
        fi
        site_compose "$domain" "$@"
        ;;
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
    *)
        cat <<'EOF'
Uso: stack.sh COMANDO [ARGUMENTOS]
  list | validate | networks
  proxy-up [--recreate]             Regenerar la configuración, conectar redes y recargar Nginx
  site-up DOMINIO [--verbose]       Construir y arrancar un sitio
  site-stop DOMINIO                 Detener todos los servicios del sitio
  site-rename DOMINIO [--apply|--drop-old] [--containers-only]  Pasar un sitio antiguo a nombres legibles
  db-up DOMINIO                     Arrancar solo la base dedicada
  db-import DOMINIO ARCHIVO.sql[.gz] --confirm-overwrite
  site-composer DOMINIO [--service S] [SUBCOMANDO OPCIONES...]
  scheduler-enable DOMINIO [--cron-file /etc/cron.d/X] [--memory 256m] [--service web]
  scheduler-disable DOMINIO
  env-split DOMINIO [--move CLAVE...] [--service web]
  env-reload DOMINIO [--service web] Recrear servicios con el entorno nuevo; config:cache en Laravel
  status [DOMINIO]                  Estado de los servicios; código 1 si alguno falla
  site DOMINIO COMANDO...           docker compose del sitio
  proxy COMANDO...                  docker compose del proxy
EOF
        ;;
esac
