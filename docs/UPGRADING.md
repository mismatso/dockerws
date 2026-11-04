# Notas de actualización

Para un servidor que ya tiene sitios creados con versiones anteriores. Ningún paso es obligatorio: los sitios existentes siguen funcionando sin cambios después de `git pull`. Cada mejora se adopta por separado; los detalles están en la [guía de adopción](ADOPTION.md).

## Procedimiento general

```bash
cd /opt/dockerws
sudo git pull
sudo bash tests/check.sh          # Opcional: no arranca contenedores ni toca sitios
sudo ./scripts/stack.sh validate
sudo ./scripts/stack.sh status
```

`validate` carga ahora también los overlays de `compose.d/`. Si no tienes ninguno, el resultado es el mismo que antes.

## Cambios de esta versión

### Overlays `compose.d/`

Nuevo mecanismo para agregar servicios o montajes sin editar `compose.yaml`. No requiere acción.

### `stack.sh site-composer`

Reemplaza el `composer install` como root documentado antes, que fallaba en sitios Laravel nuevos. Si tienes scripts propios con ese `run --rm --user 0:0 … composer install`, cámbialos por:

```bash
sudo ./scripts/stack.sh site-composer app.example.com
```

### Scheduler de Laravel sin cron del host

Por cada sitio Laravel con un archivo `/etc/cron.d/SITIO`:

```bash
sudo ./scripts/stack.sh scheduler-enable app.example.com --cron-file /etc/cron.d/SITIO
sudo ./scripts/stack.sh site app.example.com logs --since 3m scheduler
```

El traspaso se hace dentro de un mismo minuto, sin perder ni duplicar ejecuciones; ver [Adopción › Scheduler](ADOPTION.md#scheduler). Haz los sitios de uno en uno y revisa los logs del siguiente minuto antes de seguir.

### `stack.sh status`

Nuevo. Puedes agregar un cron de monitoreo, por ejemplo en `/etc/cron.d/dockerws-status`:

```cron
MAILTO=root
*/5 * * * * root out="$(/opt/dockerws/scripts/stack.sh status 2>&1)" || echo "$out" | grep -v '^OK'
```

Recibirás el aviso solo si el host tiene un MTA configurado.

### `db-import`, respaldo por sitio y `site-up` silencioso

- `stack.sh db-import SITIO archivo.sql[.gz] --confirm-overwrite` reemplaza el `sudo mysql BASE < archivo` manual en la modalidad `host`, y funciona igual en `container`. Ver [respaldos](BACKUPS.md#importar-un-dump-externo).
- `backup.sh SITIO` ya existía; ahora está documentado en el README.
- `site-up` oculta la salida de la construcción si no hay errores (`--verbose` para verla siempre).

### `app.env` y `env-reload`

Opcional, por sitio: `stack.sh env-split SITIO [--move CLAVE…]` y después `stack.sh env-reload SITIO`. Ver [Adopción › app.env](ADOPTION.md#appenv). A partir de ahora, tras editar `runtime.env` o `app.env`, usa `env-reload` en lugar de `site-up` + `config:cache` a mano.

El `env_file` opcional (`required: false`) requiere Docker Compose 2.24 o superior. El repositorio oficial de Docker que usa `host/install-debian.sh` ya trae una versión posterior.

### `deploy.sh`

Nuevo, opcional. No cambia la estructura de los sitios. Si hoy despliegas con un script propio, compáralo con la secuencia de [Desplegar Laravel y PHP](APPLICATIONS.md#desplegar-laravel-y-php) y prueba primero con `--no-migrate`. Si usabas `git checkout` en `app/`, usa `--ref`. Si usabas rsync hacia `app/`, sube a un directorio aparte y usa `--from` (ver [permisos de despliegue](#usuario-de-despliegue-sin-sudo-amplio)).

### `issue-cert.sh`

- El correo es opcional. `issue-cert.sh DOMINIO CORREO [--dry-run]` sigue funcionando igual.
- Nuevo `issue-cert.sh DOMINIO --check`: valida HTTP-01 de forma local y por el DNS público, sin depender del entorno staging. Consulta las notas de Cloudflare en el README.

### `proxy-up` sin recrear el proxy

Sin acción. El primer `proxy-up` después de actualizar se comporta igual que antes si el proxy no está corriendo. Si ya corre, conecta redes y recarga sin recrearlo. Si cambias `nginx_image` en `config/local.json`, usa `proxy-up --recreate`.

### Usuario de despliegue sin sudo amplio

Si hoy despliegas con `rsync --rsync-path="sudo rsync"` y `sudo` sin contraseña para el administrador:

1. `sudo ./host/install-deploy-user.sh deploy SITIO [SITIO…]` (ver [Usuario de despliegue](APPLICATIONS.md#usuario-de-despliegue)).
2. Cambia tu script o CI: rsync hacia `/srv/dockerws/incoming/SITIO/` como `deploy`, y luego `ssh deploy@servidor sudo /opt/dockerws/scripts/deploy.sh SITIO`.
3. Prueba un despliegue completo.
4. Retira la regla `NOPASSWD` amplia del administrador en `/etc/sudoers.d/`.

### Releases atómicos

Opcional. Los sitios con `app/` como directorio siguen desplegándose igual. Para adoptarlo: `deploy.sh DOMINIO --init-releases` (ver [Releases atómicos](ADOPTION.md#releases-atómicos)). Opciones nuevas en `config/defaults.json`: `keep_releases` (5) y `node_image`. En un sitio con releases, `site-composer` solo admite subcomandos de consulta.
