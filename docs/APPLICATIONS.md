# Preparar aplicaciones

Los ejemplos asumen que ya creaste el sitio con el asistente. No reemplaces `runtime.env` con un `.env` de desarrollo, y conserva sus credenciales al importar código.

## PHP

Copia el código a `sites/DOMINIO/app/`. La raíz web es `/var/www/html`. Están instalados `pdo_mysql`, `mysqli`, `pdo_pgsql`, `pdo_sqlite`, `mbstring`, `intl`, `zip`, `gd`, `bcmath` y OPcache. PDO recibe los parámetros mediante `getenv('DB_*')`; adapta tu aplicación para usarlos. Para MySQL del host usa `DB_SOCKET` en el DSN, no una conexión TCP a `localhost`.

El código es de solo lectura. Para archivos modificables utiliza `/var/www/writable` (persistente en `sites/DOMINIO/writable`) o agrega montajes explícitos al Compose. Mantén directorios legibles por UID 33 y archivos de código normalmente 0644. No apliques `chmod 777`.

## Laravel

Copia el proyecto completo a `app/`; la raíz web será `app/public`. Incluye `vendor/` construido para la versión de PHP elegida o instala las dependencias con la imagen del sitio:

```bash
sudo ./scripts/stack.sh site-composer app.example.com
```

Sin argumentos ejecuta `composer install --no-dev --prefer-dist --optimize-autoloader --no-interaction`. Puedes pasar otro subcomando y sus opciones (`site-composer app.example.com update laravel/framework`). Si tu Compose define el servicio PHP con otro nombre, usa `--service NOMBRE` antes del subcomando.

Por qué no basta un `composer install` como root: el contenedor no tiene capabilities, así que su root no tiene `DAC_OVERRIDE`. Puede escribir `app/` (propiedad de root), pero no leer `storage/` ni escribir `bootstrap/cache` (33:33, `0750`). Los scripts de Laravel (`package:discover`) fallan con «The bootstrap/cache directory must be present and writable» o «Please provide a valid cache path». `site-composer` hace:

1. `composer … --no-scripts` como `0:0`, con `app/` montado escribible solo en ese contenedor temporal;
2. `chown -R 33:33 storage bootstrap/cache` en el host, sin seguir enlaces simbólicos;
3. `composer run-script post-autoload-dump` como `33:33` con el código de solo lectura (ejecuta `package:discover`).

`post-update-cmd` (por ejemplo `vendor:publish --tag=laravel-assets`) no se ejecuta: escribiría en el código, que es de solo lectura para la aplicación. Publica esos assets al construir el código. Los subcomandos de consulta (`show`, `outdated`, `audit`, `validate`…) corren como 33:33 sin montar el código escribible. La caché de paquetes queda en `/var/cache/dockerws/composer/ID` (`0700`).

El paso como root es una tarea administrativa temporal con código escribible: usa solo dependencias de confianza. El servicio web sigue corriendo como UID 33 con código de solo lectura. También puedes construir `vendor/` y los assets en CI; Node no forma parte de la imagen PHP.

### Variables de entorno

Hay dos archivos, ambos privados (`0600`):

- `runtime.env`: valores que genera DockerWS (`APP_KEY`, `DB_*`, URL, entorno, `LOG_CHANNEL=stderr`).
- `app.env`: variables propias de la aplicación (`APP_NAME`, `APP_LOCALE`, `SESSION_SECURE_COOKIE`, `MAIL_*`, claves de servicios externos). Compose lo carga **después** de `runtime.env`, así que una clave repetida en `app.env` reemplaza a la otra.

Después de editar cualquiera de los dos:

```bash
sudo ./scripts/stack.sh env-reload app.example.com
```

`env-reload` recrea los servicios cuyo entorno cambió (web, scheduler) y, en Laravel, ejecuta `config:cache` para que la aplicación deje de usar la configuración anterior. Avisa, solo con el nombre de la clave, si `app.env` redefine algo de `runtime.env`. Un `site-up` no basta: no regenera la caché de configuración.

En una migración **conserva el APP_KEY original** para poder descifrar datos existentes. El generador crea una clave nueva solo como valor inicial; no ejecutes `key:generate` sobre una aplicación migrada. Retira del código cualquier `.env` anterior para no mezclar configuraciones.

Después de importar, verifica permisos de `app/storage` y `app/bootstrap/cache`: propietario 33:33, directorios 0750 y archivos escribibles por el propietario. El asistente prepara esos directorios y `site-composer` los corrige, pero rsync o una restauración pueden cambiar sus permisos.

```bash
sudo chown -R 33:33 sites/app.example.com/app/storage sites/app.example.com/app/bootstrap/cache
sudo ./scripts/stack.sh site-up app.example.com
sudo ./scripts/stack.sh site app.example.com exec --user 33:33 web php artisan config:clear
# Cuando hayas revisado los cambios de esquema y respaldado:
sudo ./scripts/stack.sh site app.example.com exec --user 33:33 web php artisan migrate --force
sudo ./scripts/stack.sh site app.example.com exec --user 33:33 web php artisan config:cache
```

Configura `trustProxies` en `bootstrap/app.php` para aceptar los encabezados `X-Forwarded-*` del proxy. En esta topología el único ingreso web publicado es Nginx; puedes usar `at: '*'` mientras mantengas esa restricción. No añadas puertos públicos al backend. [Proxies en Laravel](https://laravel.com/framework/docs/requests).

La configuración inicial usa sesiones y caché en archivos, y cola `sync`. Workers de colas y Redis son extensiones de la hoja de ruta. Si los agregas a mano, dales el mismo entorno y permisos, y detén todos los escritores al restaurar datos.

### Scheduler

El scheduler de Laravel corre en un servicio `scheduler` propio con `php artisan schedule:work`. No se necesita un cron en el host. Al crear el sitio:

```bash
sudo ./scripts/create-site.sh --domain app.example.com --type laravel --database mysql --db-mode host --scheduler
```

El asistente interactivo también lo pregunta. En un sitio existente:

```bash
sudo ./scripts/stack.sh scheduler-enable app.example.com
```

El servicio se escribe en `sites/DOMINIO/compose.d/50-scheduler.yaml`, un overlay que Compose carga después de `compose.yaml`. Se deriva del servicio `web`:

- usa la misma imagen y el mismo `env_file`;
- corre como UID 33, sin capabilities, con el código de solo lectura y los mismos montajes escribibles;
- tiene `restart: unless-stopped` y su propio límite de memoria (`scheduler_memory`, 256 MiB por defecto; `--memory` para cambiarlo);
- no tiene alias de red, puertos ni healthcheck HTTP, así que el proxy nunca le envía tráfico.

`compose.yaml` no se modifica. Si tu servicio PHP tiene otro nombre, usa `--service NOMBRE`.

- `site-stop` también detiene el scheduler, y `site-up` lo arranca.
- La salida de las tareas queda en `stack.sh site DOMINIO logs scheduler`.
- `stack.sh status` muestra el estado de todos los servicios y termina con código 1 si alguno no está corriendo. Los servicios con `replicas: 0` (por ejemplo, uno que solo construye la imagen base) se listan con `-` y no cuentan como caídos. Puedes ejecutarlo desde cron o desde tu monitoreo para enterarte si el scheduler se cae.
- Las tareas no se ejecutan durante `artisan down`, salvo las marcadas con `evenInMaintenanceMode()`.
- Para quitarlo: `stack.sh scheduler-disable DOMINIO`.

`storage:link` no puede escribir sobre el código montado de solo lectura; `deploy.sh` crea el enlace `public/storage` desde el host.

`scheduler-enable` se niega a continuar si encuentra en `/etc/cron.d/` un cron de `schedule:run` para ese sitio, porque las tareas se ejecutarían dos veces. Para pasar de ese cron al servicio sin perder ni duplicar ejecuciones, consulta la [guía de adopción](ADOPTION.md#scheduler).

## Desplegar Laravel y PHP

`deploy.sh` reemplaza los procedimientos propios de cada proyecto:

```bash
# Código ya subido al servidor (por ejemplo con rsync desde CI)
sudo ./scripts/deploy.sh app.example.com --from /srv/dockerws/incoming/app.example.com

# Checkout de git en app/ (con su llave de despliegue configurada para root)
sudo ./scripts/deploy.sh app.example.com --ref main

# Sin origen: vuelve a ejecutar composer, migraciones y cachés sobre el código actual
sudo ./scripts/deploy.sh app.example.com
```

Antes de tocar el sitio, comprueba el origen, obtiene la referencia de git, construye la imagen y ejecuta el gancho `pre-deploy`. Si algo falla en esta etapa, el sitio sigue en línea sin cambios. Después, en Laravel:

1. `artisan down --retry=60`, si `web` está corriendo;
2. respaldo de la base del sitio (`backup.sh DOMINIO`), salvo con `--skip-backup` o si no hay base;
3. sincroniza el código: `rsync --delete`, o `git checkout --detach` del commit obtenido;
4. ejecuta `site-composer` (`--no-composer` para omitirlo);
5. `migrate --force` (`--no-migrate` para omitirlo) y `optimize`;
6. recrea `web`, el scheduler y los demás servicios que estaban corriendo, salvo `db`;
7. `artisan up` y el gancho `post-deploy`.

En sitios PHP no hay mantenimiento, migraciones ni respaldo, y composer solo corre si existe `composer.json`. WordPress no está soportado: se actualiza con su propio proceso.

Lo que se conserva entre despliegues:

- **`--from`** nunca copia ni borra `.env`, `.git/`, `storage/`, `bootstrap/cache/`, `public/storage` ni `node_modules/`. Tampoco `vendor/` cuando composer se ejecuta (con `--no-composer`, `vendor/` se toma del origen). Para evitar borrar `app/` por error, se niega si el origen está vacío o, en Laravel, si le faltan `artisan` o `composer.json`.
- **Permisos:** los archivos copiados quedan de root, legibles por 33 y sin escritura de grupo ni otros. `storage/` y `bootstrap/cache` vuelven a 33:33.
- **`public/storage`:** se crea como enlace relativo a `storage/app/public`, porque `storage:link` no puede escribir en el código de solo lectura.
- **`--ref`:** se niega si `app/` tiene cambios locales en archivos versionados.

**Si falla después de `artisan down`:** el sitio queda en mantenimiento a propósito, porque el código o la base pueden estar a medias. Corrige y vuelve a desplegar, o restaura el respaldo recién creado. Un solo despliegue por sitio a la vez.

**Ganchos:** `sites/DOMINIO/hooks/pre-deploy` y `post-deploy`, ejecutables, propiedad de root y sin escritura de grupo ni otros (el directorio `hooks/` también). Si no, el despliegue se detiene. Reciben `DOCKERWS_SITE`, `DOCKERWS_APP`, `DOCKERWS_SOURCE` y `DOCKERWS_REF`. Los ganchos dentro del código desplegado no se ejecutan, porque correrían como root. Para tareas de la aplicación, llama a `stack.sh site DOMINIO run --rm --user 33:33 web php artisan …` desde un gancho.

**Reiniciar `web`:** `deploy.sh` siempre recrea los contenedores, así que no hace falta hacerlo a mano. La imagen usa `opcache.validate_timestamps=1`: tras una copia manual de archivos PHP, OPcache detecta los cambios en unos 2 segundos. Aun así, reinicia `web` y el scheduler (`stack.sh site DOMINIO restart web scheduler`) para no servir una mezcla de versiones ni dejar workers con el código anterior en memoria. No se usa `validate_timestamps=0`: obligaría a reiniciar después de cualquier cambio y la imagen la comparten todos los sitios.

**Assets (Node):** Node no está en la imagen PHP. Compila los assets (`npm ci && npm run build`) en CI o en la máquina de desarrollo e incluye `public/build/` en el origen. Ejemplo con un contenedor desechable antes de desplegar:

```bash
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/app" -w /app node:22-bookworm-slim sh -c 'npm ci && npm run build'
```

### Usuario de despliegue

Para no dar `sudo` sin contraseña al administrador (por ejemplo, el antiguo `rsync --rsync-path="sudo rsync"`), crea un usuario dedicado:

```bash
sudo adduser --disabled-password deploy          # Agrega su llave pública a ~deploy/.ssh/authorized_keys
sudo ./host/install-deploy-user.sh deploy app.example.com
```

El instalador:

- crea `/srv/dockerws/incoming/DOMINIO`, propiedad de `deploy` (`0750`), dentro de una base de root (`0755`). La ruta base se configura con `deploy_incoming_dir`;
- instala `/etc/sudoers.d/dockerws-deploy-deploy`, validado con `visudo`, que solo permite `deploy.sh DOMINIO [opciones]` para los sitios indicados;
- se niega si el checkout, `scripts/` o `deploy.sh`/`deploy.py` no son de root o son escribibles por otros. Si `deploy` pudiera modificarlos, la regla de sudo sería un root completo.

Desde CI o la máquina de desarrollo:

```bash
rsync -az --delete --exclude .git --exclude node_modules ./ deploy@servidor:/srv/dockerws/incoming/app.example.com/
ssh deploy@servidor sudo /opt/dockerws/scripts/deploy.sh app.example.com
```

Cuando `deploy.sh` se invoca con sudo desde un usuario que no es root:

- el único origen admitido es su carpeta de subida (`--from` se puede omitir);
- `--ref` sigue disponible;
- `--skip-backup` se rechaza;
- la carpeta debe pertenecer a ese usuario y la base a root, sin enlaces simbólicos.

El usuario de despliegue no escribe en `sites/`, `app/`, `runtime.env` ni `secrets/`. El código se copia a `app/` como root:root y sigue de solo lectura en el contenedor. Los ganchos solo se toman de `sites/DOMINIO/hooks/` (de root).

Límite de confianza: quien despliega decide el código que se ejecuta como UID 33 en el contenedor, y los scripts de composer, que corren en un contenedor temporal como root sin capabilities con `app/` escribible. Es el mismo nivel de confianza de cualquier despliegue, pero ya sin root en el host.

Sin releases, el rsync reemplaza archivos mientras Apache atiende peticiones, aunque con la aplicación en mantenimiento. Con [releases atómicos](#releases-atómicos) el código nuevo se prepara aparte y se activa de una vez.

Con sudo no se permiten `--init-releases`, `--revert-releases` ni `--force`. `--rollback` sí está permitido: el usuario de despliegue ya decide qué código se publica, así que volver a un release anterior no le da un privilegio nuevo y permite reaccionar rápido ante un incidente. La comprobación de migraciones se aplica igual. Si la base tiene migraciones que el release destino no conoce, la vuelta atrás requiere `--force`, y esa decisión queda en manos de root. Para que solo root pueda volver atrás, pon `"deploy_user_rollback": false` en `config/local.json`.

### Releases atómicos

Opcional. Cada despliegue crea un directorio nuevo y `app` pasa a ser un enlace al release activo:

```text
sites/DOMINIO/
  app -> releases/20261104T063322Z
  releases/
    20261104T063055Z/     Release anterior, intacto
    20261104T063322Z/     Código (root), vendor/, bootstrap/cache (33:33), storage/ vacío
    history.log           Activaciones y vueltas atrás, con el usuario que las hizo
  shared/storage/         storage/ de Laravel, común a todos los releases
  repo.git/               Mirror opcional para --ref
  compose.d/90-releases.yaml
```

Se activa una vez por sitio, con un mantenimiento breve:

```bash
sudo ./scripts/deploy.sh app.example.com --init-releases
```

Mueve `app/` a `releases/FECHA` y `app/storage` a `shared/storage`, crea el enlace y el overlay `90-releases.yaml` (que monta `shared/storage` en los servicios de `compose.yaml`), actualiza los overlays de workers de DockerWS (`50-*.yaml`) y recrea los servicios. Si algo falla, deshace cada paso. `--revert-releases` vuelve a la estructura anterior con el release activo; los demás quedan en `releases/` para que los borres a mano.

Con releases, `deploy.sh` admite los mismos orígenes y opciones:

1. **Con el sitio en línea:** copia el origen a `releases/.pending-*` (o exporta el commit con `git archive` desde `repo.git`), quita `.env`, `.git/` y `storage/`, ejecuta composer sobre ese directorio, compila assets si se pidió `--assets` y ejecuta el gancho `pre-deploy`. Si algo falla, se descarta el directorio y el sitio no se entera.
2. `artisan down` y respaldo de la base.
3. `migrate --force` y `optimize` con el release nuevo montado en un contenedor temporal, mientras `app` sigue apuntando al anterior.
4. Renombra el release, guarda sus metadatos (`.dockerws-release.json`: origen, commit, release anterior, respaldo y migraciones) y cambia el enlace con un `rename` atómico.
5. Recrea los servicios, `artisan up`, gancho `post-deploy` y limpieza: se conservan los últimos `keep_releases` (5), más el activo y el anterior.

Si falla antes de migrar, el sitio vuelve solo a estar en línea con el release anterior. Si falla en las migraciones, queda en mantenimiento con el release anterior, para que decidas si corregir y redesplegar o restaurar el respaldo.

**Volver atrás:**

```bash
sudo ./scripts/deploy.sh app.example.com --rollback                    # Al release anterior
sudo ./scripts/deploy.sh app.example.com --rollback 20261104T063055Z   # A uno concreto (también hacia adelante)
```

Regenera las cachés de Laravel en el release destino, cambia el enlace y recrea los servicios. **No revierte migraciones:** compara la tabla `migrations` con las migraciones que conoce el release destino (incluidas las de paquetes en `vendor/`). Si la base tiene alguna que el destino no conoce, se niega y muestra el respaldo tomado antes del despliegue. Si sabes que las migraciones son compatibles con el código anterior (por ejemplo, solo agregan una tabla), root puede usar `--force`. Si no, restaura el respaldo con `restore.sh` y luego vuelve atrás.

**`--ref` con releases** usa un mirror en lugar de un checkout en `app/`:

```bash
sudo git clone --mirror git@github.com:example/app.git /opt/dockerws/sites/app.example.com/repo.git
sudo ./scripts/deploy.sh app.example.com --ref main
```

**Sin mantenimiento:** `--no-maintenance` omite `artisan down`. Úsalo solo si las migraciones son compatibles con el código que sigue en línea (agregar tablas o columnas con valor por omisión, no renombrar ni borrar): durante unos segundos, el código anterior trabaja con la base ya migrada.

**Assets:** `--assets` ejecuta `npm ci && npm run build` en el release en construcción, con la imagen `node_image` (por defecto `node:22-bookworm-slim`), como root sin capabilities, y borra `node_modules/` al terminar. Sin releases, compila los assets fuera del servidor.

**Composer:** los releases no se modifican en su lugar. `site-composer` solo admite subcomandos de consulta (`show`, `outdated`, `audit`…); para cambiar dependencias, actualiza `composer.lock` en el origen y despliega.

**Tiempo fuera de servicio:** igual que sin releases, el mantenimiento dura lo que el respaldo, las migraciones y la recreación de los contenedores (unos segundos en un sitio pequeño). Mientras se recrea `web`, el proxy puede responder 502 durante unos instantes. Composer, la copia del código y los assets ya no forman parte de esa ventana.

## WordPress

Descarga el código desde [WordPress.org](https://wordpress.org/download/) o importa tu sitio a `app/`. Conserva/adapta el `wp-config.php` generado, que lee credenciales, URL HTTPS y salts de `runtime.env`.

Si migras un sitio, ajusta `$table_prefix` al prefijo de sus tablas, copia `wp-content` y verifica las URL almacenadas. Para cambiar dominios utiliza una herramienta que respete datos serializados, como WP-CLI, después de respaldar. Si quieres conservar sesiones existentes, conserva también los salts de origen.

Solo `wp-content/uploads` es escribible en ejecución. La instalación y actualización de plugins, temas y core se hace mediante tu proceso de despliegue; el panel no modifica código. Revisa cada plugin por si requiere un directorio de caché escribible y monta solo ese directorio. No hagas todo `app/` escribible para resolver permisos.

`wp-cron` conserva el comportamiento normal de WordPress; el cron del host que instala este proyecto corresponde a respaldos. Puedes sustituir wp-cron por una tarea controlada si tu sitio lo necesita.

## Estáticos

Copia HTML, CSS, JavaScript y assets a `app/`. La plantilla sirve archivos existentes y devuelve 404 para los demás. Para una SPA ajusta `sites/DOMINIO/nginx.conf` a `try_files $uri $uri/ /index.html;`. Los archivos generados pertenecen a tu instalación y se pueden editar sin cambiar las plantillas públicas.

## Actualizaciones

Respalda y prueba primero. Para actualizar dentro de las ramas elegidas, descarga/reconstruye y recrea cada sitio durante mantenimiento. No hay un actualizador automático de imágenes ni cambios mayores automáticos. Las bases del host se actualizan con sus procedimientos de Debian/proveedor y afectan a todos los sitios que las usan.
