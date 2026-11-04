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

Nuevo, opcional. No cambia la estructura de los sitios. Si hoy despliegas con un script propio, compáralo con la secuencia de [Desplegar Laravel y PHP](APPLICATIONS.md#desplegar-laravel-y-php) y prueba primero con `--no-migrate`. Si usabas `git checkout` en `app/`, usa `--ref`. Si usabas rsync hacia `app/`, sube a un directorio aparte y usa `--from`.
