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

Los servicios con `replicas: 0` o `scale: 0` (por ejemplo, un servicio `base` que solo construye la imagen) se listan con `-` y no cuentan como fallas.

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

### Releases atómicos y `--rollback`

Opcional. Los sitios con `app/` como directorio siguen desplegándose igual. Para adoptarlo: `deploy.sh DOMINIO --init-releases` (ver [Releases atómicos](ADOPTION.md#releases-atómicos)). Opciones nuevas en `config/defaults.json`: `keep_releases` (5), `deploy_user_rollback` (`true`: el usuario de despliegue puede usar `--rollback` sin `--force`) y `node_image`. En un sitio con releases, `site-composer` solo admite subcomandos de consulta.

### Administradores con sudo completo en `deploy.sh`

Antes, `deploy.sh` trataba como usuario de despliegue a cualquiera que lo invocara con sudo (también con `sudo -i` o `sudo su`). Ahora solo limita a quien tiene la regla `/etc/sudoers.d/dockerws-deploy-USUARIO` que crea `host/install-deploy-user.sh`. Un administrador puede usar `--init-releases`, `--force`, `--skip-backup` y `--from` con cualquier origen sin abrir `sudo su -`. No requiere acción.

### `php.ini-production` en la imagen PHP

La imagen parte ahora de `php.ini-production`, y `images/php-apache/php.ini` se aplica encima. Cambian, entre otros, `short_open_tag=Off`, `error_reporting` (sin avisos de obsolescencia) y `zend.assertions=-1`. `variables_order` se mantiene en `EGPCS`, así que `$_ENV` sigue disponible. Se aplica al reconstruir (`stack.sh site-up SITIO` o el siguiente `deploy.sh`). Antes, comprueba que el código no use etiquetas `<?` cortas:

```bash
grep -rlE '<\?($|[[:space:]])' --include='*.php' /opt/dockerws/sites/app.example.com/app | grep -v /vendor/
```

Si una aplicación antigua las necesita, agrega `short_open_tag=On` a `images/php-apache/php.ini`. Ese cambio afecta a todos los sitios.

### `HOME` en `stack.sh site … exec` y `run`

`stack.sh site SITIO exec|run …` pasa `-e HOME=/tmp`. El sistema de archivos del contenedor es de solo lectura, y herramientas como `artisan tinker` necesitan un `HOME` escribible:

```bash
sudo ./scripts/stack.sh site app.example.com exec web php artisan tinker
```

Para usar otro valor, pasa `-e HOME=…` después; Docker usa el último.

### Imagen PHP sobre Debian 13 (trixie)

Los sitios nuevos construyen la imagen PHP sobre `php:VERSIÓN-apache-trixie`. Antes usaban bookworm, aunque el host fuera Debian 13. La versión de Debian se configura con `php_debian` en `config/local.json`; por ejemplo, `"php_debian": "bookworm"` mantiene la anterior. El `Dockerfile` compila en las dos.

Los sitios existentes no cambian: su `compose.yaml` fija `PHP_IMAGE` y el generador no lo sobrescribe. Para pasar un sitio a trixie:

```bash
cd /opt/dockerws/sites/app.example.com
sudo cp compose.yaml compose.yaml.bak
sudo sed -i 's/-apache-bookworm"/-apache-trixie"/' compose.yaml
sudo /opt/dockerws/scripts/stack.sh site-up app.example.com
sudo /opt/dockerws/scripts/stack.sh status app.example.com
```

`site-up` reconstruye la imagen y recrea `web` y los workers, que usan la misma imagen. PHP y sus extensiones siguen en la misma versión; cambian las bibliotecas del sistema (ICU, OpenSSL, libxml2, libzip, entre otras). Si el sitio usa una imagen propia (por ejemplo, un servicio `base` con `./image`), cambia el `FROM` de esa imagen. Para volver atrás, restaura `compose.yaml.bak` y ejecuta `site-up` otra vez.

### Nombres legibles para sitios nuevos

Los sitios nuevos toman su ID y el nombre de su base del dominio, con un sufijo corto del hash. Por ejemplo, `app.example.com` produce:

- contenedores `dws-app-example-com-2805-web-1` y `dws-app-example-com-2805-db-1`;
- base y usuario `dws_app_example_com_2805`.

Antes eran `dockerws-sHASH-web-1` y `dwsHASH`, con 12 caracteres hexadecimales.

Los sitios existentes no cambian y no requieren acción. `site.json` guarda el ID y la base, y `compose.yaml` conserva el nombre del proyecto (`dockerws-ID`). Para pasarlos a los nombres nuevos, usa `stack.sh site-rename` (ver [Adopción › Nombres legibles](ADOPTION.md#nombres-legibles)).

Para saber a qué sitio pertenece cada contenedor existente:

```bash
docker ps --format 'table {{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}'
```

En MySQL y MariaDB, el permiso de la base escapa `_` (`dws\_app\_…`). Sin ese escape, `_` actúa como comodín en `GRANT` y el permiso podría cubrir otras bases con nombres parecidos.

### Proxy `dws-proxy`

El contenedor del proxy se llama ahora `dws-proxy` (proyecto de Compose `dws-proxy`). Antes era `dockerws-proxy-proxy-1`. En una instalación nueva, `proxy-up` lo crea con el nombre nuevo.

En una instalación existente, el proxy anterior sigue funcionando hasta que decidas reemplazarlo:

- `proxy-up` conecta las redes y recarga Nginx igual que antes, y avisa que el proxy aún no se reemplazó;
- `reload-proxy.sh` (usado al emitir y renovar certificados) recarga el proxy que esté en marcha;
- `stack.sh status` lo da por bueno.

Para reemplazarlo, en un momento de poco tráfico:

```bash
sudo ./scripts/stack.sh proxy-up --recreate
sudo ./scripts/stack.sh status
```

`--recreate` quita `dockerws-proxy-proxy-1` y crea `dws-proxy` con la misma configuración y los mismos certificados. Todos los sitios quedan sin servicio unos segundos. Las redes de los sitios (`dockerws-edge-ID`) no cambian.

Si tienes scripts o un cron que usan el nombre del contenedor (por ejemplo, `docker exec dockerws-proxy-proxy-1 …`), cámbialos a `dws-proxy`, o mejor, a `stack.sh proxy exec proxy …`.


### `/.well-known/` llega a la aplicación

Antes, la imagen PHP respondía 403 a cualquier ruta bajo `/.well-known/` que no existiera en disco, aunque la aplicación la atendiera. Por ejemplo, el `security.txt` de una ruta de Laravel (RFC 9116), `change-password`, `openid-configuration`, `assetlinks.json` o `apple-app-site-association`. La regla de Apache que niega los archivos ocultos se aplicaba al primer componente inexistente de la ruta, `.well-known`. Ahora esas peticiones llegan a `index.php`, igual que en el proxy, que ya dejaba pasar `/.well-known/`.

Además, Apache niega ahora el contenido de cualquier directorio oculto dentro del código servido, por ejemplo `public/.git/config`. Antes solo lo bloqueaba el proxy. `.env`, `.htaccess`, `*.sql`, `composer.json` y los demás archivos ocultos siguen negados. El desafío ACME no cambia: Nginx lo atiende desde su webroot y no lo envía a la aplicación.

Se aplica al reconstruir la imagen de cada sitio. `site-up` reconstruye y recrea solo el sitio indicado, sin tocar a los demás ni al proxy. Hazlo de uno en uno:

```bash
sudo ./scripts/stack.sh site-up app.example.com
sudo ./scripts/stack.sh status app.example.com
curl -I https://app.example.com/.well-known/security.txt
```

El siguiente `deploy.sh` del sitio también reconstruye la imagen. Después del cambio, `curl -I` debe devolver la respuesta de la aplicación (200, o 404 si no define esa ruta) en lugar de 403. Si el sitio usa una imagen propia (por ejemplo, un servicio `base` con `./image`), copia en ella el `apache.conf` nuevo.

Si un sitio montaba su propio `000-default.conf` con un overlay para evitar el 403, por ejemplo `compose.d/70-apache-wellknown.yaml` con `apache/000-default.conf`, ya puede retirarlo después de reconstruir:

```bash
cd /opt/dockerws/sites/app.example.com
sudo mv compose.d/70-apache-wellknown.yaml /root/70-apache-wellknown.yaml.bak
sudo /opt/dockerws/scripts/stack.sh site-up app.example.com
curl -I https://app.example.com/.well-known/security.txt
```

Si el resultado no es el esperado, devuelve el overlay a `compose.d/` y ejecuta `site-up` otra vez. Antes de retirarlo, compara ese `000-default.conf` con `images/php-apache/apache.conf` por si tenía otros cambios.

### Primer despliegue sin `proxy-up` previo

`deploy.sh` crea la red frontal del sitio (`dockerws-edge-ID`) si aún no existe, antes de respaldar o sincronizar. Antes, el primer `deploy.sh DOMINIO --from DIR` justo después de `create-site.sh` fallaba en composer con «network dockerws-edge-… declared as external, but could not be found», con el código ya copiado. Había que ejecutar antes `stack.sh networks` o `proxy-up`. `site-composer` y `env-reload` también la crean. La red lleva la misma etiqueta `org.dockerws.site` que crea `stack.sh networks`; si ya existe una red con ese nombre que no es del sitio, el comando se detiene sin tocar nada. No requiere acción.

### `issue-cert.sh --check` y la caché DNS del host

Cuando un nombre no resuelve, `--check` recuerda que el resolvedor del host puede guardar la respuesta negativa durante el TTL negativo de la zona y, si existe `resolvectl`, sugiere `resolvectl flush-caches`. No vacía la caché por su cuenta. Ver [Inicio rápido](../README.md#inicio-rápido).
