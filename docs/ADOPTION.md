# Adoptar mejoras en sitios existentes

Los sitios creados con versiones anteriores siguen funcionando sin cambios. Cada mejora se adopta por separado, con un comando que agrega un archivo en `sites/DOMINIO/compose.d/` y nunca reescribe `compose.yaml`. Esto también sirve en instalaciones con el `compose.yaml` editado a mano (por ejemplo, con un servicio `base` y una imagen propia en `./image`).

Antes de empezar:

```bash
cd /opt/dockerws
sudo git pull
sudo ./scripts/stack.sh validate
sudo ./scripts/backup.sh app.example.com
```

## Overlays de Compose

Todos los comandos de DockerWS (`stack.sh`, `backup.sh`, `restore.sh`, `deploy.sh`) cargan `compose.yaml` y después, en orden alfabético, los archivos `compose.d/*.yaml`. Un overlay puede agregar servicios o cambiar un montaje: los volúmenes con el mismo destino se reemplazan y `env_file` se agrega al final. Para ver el resultado combinado:

```bash
sudo ./scripts/stack.sh site app.example.com config
```

Si ejecutas `docker compose` directamente, sin `stack.sh`, debes pasar también cada overlay con `-f`.

## Scheduler

`scheduler-enable` deriva el servicio del `web` resuelto por `docker compose config --no-interpolate`, así que respeta los cambios hechos a mano: imagen propia, montajes adicionales, otro usuario. No copia valores de `runtime.env` al overlay. Si el servicio PHP no se llama `web`, indica `--service NOMBRE`.

### Sitio sin cron del host

```bash
sudo ./scripts/stack.sh scheduler-enable app.example.com
sudo ./scripts/stack.sh site app.example.com logs -f scheduler
```

### Sitio con `/etc/cron.d/SITIO`

```bash
sudo ./scripts/stack.sh scheduler-enable app.example.com --cron-file /etc/cron.d/app-example
```

El comando:

1. comprueba que el archivo solo contenga el `schedule:run` de ese sitio. Si contiene otras tareas, se detiene: sepáralas antes en otro archivo;
2. construye la imagen y crea el contenedor `scheduler` sin arrancarlo;
3. espera a que pase el segundo 5 del minuto actual. Para entonces el cron de ese minuto ya se disparó;
4. renombra el archivo de cron a un nombre que cron ignora, en una operación atómica, y arranca el scheduler. `schedule:work` ejecuta su primera ronda al comenzar el minuto siguiente, así que no se pierde ni se duplica ningún minuto;
5. mueve el archivo a `/var/lib/dockerws/cron-retirado/` con la fecha en el nombre.

Si el scheduler no arranca, el archivo de cron se restituye a su ubicación original. Si arrancara después del cambio de minuto (no debería: solo falta iniciar un contenedor ya creado), el comando lo avisa para que revises los logs.

Tareas con `withoutOverlapping()` o `onOneServer()` usan locks en la caché. Con la caché en archivos (configuración inicial), el scheduler y `web` comparten `storage/`, así que los locks siguen funcionando.

Para volver atrás:

```bash
sudo ./scripts/stack.sh scheduler-disable app.example.com
sudo mv /var/lib/dockerws/cron-retirado/app-example.FECHA /etc/cron.d/app-example
```

## app.env

Los sitios nuevos ya cargan `app.env`. En un sitio existente:

```bash
sudo ./scripts/stack.sh env-split app.example.com
```

Crea `app.env` con ejemplos comentados y el overlay `compose.d/10-app-env.yaml`, que agrega `app.env` (opcional) después de `runtime.env` en el servicio `web`. Si el sitio ya tiene el overlay del scheduler, también lo actualiza. Usa `--service NOMBRE` si el servicio PHP no se llama `web`.

Para mover a `app.env` las variables que agregaste a mano a `runtime.env`:

```bash
sudo ./scripts/stack.sh env-split app.example.com --move APP_NAME APP_LOCALE SESSION_SECURE_COOKIE MAIL_MAILER MAIL_HOST
sudo ./scripts/stack.sh env-reload app.example.com
```

Antes de mover, guarda una copia `runtime.env.FECHA.bak` (`0600`). Se niega a mover credenciales generadas (`APP_KEY`, `DB_*`, salts de WordPress). Nunca imprime valores. Cuando confirmes que todo funciona, borra la copia.

## Despliegue

`deploy.sh` no requiere cambios en el sitio. Prueba primero sin migraciones y revisa la salida:

```bash
sudo ./scripts/deploy.sh app.example.com --from /srv/dockerws/incoming/app.example.com --no-migrate
```

Si tu proceso actual ejecuta pasos propios (limpiar cachés, publicar assets, notificar), pásalos a `sites/DOMINIO/hooks/pre-deploy` o `post-deploy` (root, `0755`). Para un usuario de despliegue sin `sudo` amplio, sigue [Usuario de despliegue](APPLICATIONS.md#usuario-de-despliegue).

Si tu `compose.yaml` editado a mano define el servicio PHP con otro nombre, usa `--service NOMBRE`. Al final se recrean el servicio web, el scheduler y los servicios que ya estaban corriendo. `db` y los servicios detenidos (por ejemplo, un servicio `base` que solo construye la imagen) no se tocan.

## Releases atómicos

Requiere `deploy.sh` y un sitio Laravel o PHP. Revisa antes:

- que nada fuera de DockerWS escriba en `app/` o `app/storage` (tareas de cron del host, scripts propios): después de la conversión esas rutas cambian a `releases/…` y `shared/storage`;
- si tu `compose.yaml` monta `app/storage` en algún servicio, `--init-releases` crea `compose.d/90-releases.yaml` para montar `shared/storage` en su lugar. Revísalo con `stack.sh site DOMINIO config`. Los volúmenes que no sean `app/storage` (por ejemplo `./image` o datos propios) no se tocan;
- que haya un respaldo reciente.

```bash
sudo ./scripts/backup.sh app.example.com
sudo ./scripts/deploy.sh app.example.com --init-releases
sudo ./scripts/stack.sh status app.example.com
sudo ./scripts/deploy.sh app.example.com --from /srv/dockerws/incoming/app.example.com --no-migrate
```

Si `app/` era un checkout de git, crea el mirror (`git clone --mirror URL sites/DOMINIO/repo.git`) para seguir usando `--ref`. Para volver atrás con la estructura anterior: `deploy.sh DOMINIO --revert-releases`.

## Nombres legibles

Opcional. Pasa un sitio creado con versiones anteriores a los nombres que se derivan del dominio:

- **contenedores e imágenes:** `dockerws-sHASH-*` → `dws-app-example-com-2805-*`;
- **base y usuario** (MySQL y MariaDB): `dwsHASH` → `dws_app_example_com_2805`.

El ID de `site.json`, las redes del proxy y el alias `web-ID` no cambian: no se ven en `docker ps` y el proxy sigue igual. Antes de empezar, actualiza DockerWS en el servidor; las versiones anteriores no aceptan `_` en el nombre de la base.

```bash
sudo ./scripts/stack.sh site-rename app.example.com            # Solo muestra el plan
sudo ./scripts/stack.sh site-rename app.example.com --apply    # Corte breve del sitio
sudo ./scripts/stack.sh status app.example.com
```

Sin `--apply` no cambia nada: muestra qué se renombra y avisa si un archivo de cron menciona el nombre viejo.

`--apply`, en orden:

1. Guarda una copia de `site.json`, `compose.yaml`, `compose.d/`, `runtime.env`, `app.env` y `secrets/init.sql` en `.rename-antes.tgz`.
2. Cambia el nombre del proyecto en `compose.yaml` y `compose.d/`, incluidas las imágenes que lo usan (por ejemplo, el scheduler o un servicio `base`). Los volúmenes del proyecto viejo se declaran `external` con su nombre actual, así que los datos no se mueven.
3. Construye las imágenes con el nombre nuevo mientras el sitio sigue en línea.
4. Comienza el corte: quita los contenedores viejos.
5. Con base MySQL o MariaDB:
   - hace un respaldo con `backup.sh`;
   - crea la base y el usuario nuevos con la misma contraseña;
   - actualiza `DB_DATABASE` y `DB_USERNAME`;
   - importa el respaldo y compara la cantidad de tablas.
6. Arranca el sitio (si estaba en marcha) y, en Laravel con la configuración en caché, ejecuta `config:cache`.

Si algo falla, vuelve solo al estado anterior: borra la base y el usuario nuevos, restaura los archivos y arranca el sitio con los nombres viejos. En una prueba con MySQL del host, el corte fue de unos 7 segundos más el tiempo del respaldo y la importación.

Cuando compruebes que todo funciona, borra lo viejo (base, usuario, tags de imagen y redes):

```bash
sudo ./scripts/stack.sh site-rename app.example.com --drop-old
```

Hasta ese momento puedes volver atrás a mano:

```bash
cd /opt/dockerws/sites/app.example.com
sudo docker compose -p dws-app-example-com-2805 down   # Contenedores con el nombre nuevo
sudo tar -xzf .rename-antes.tgz
sudo rm .rename.json
sudo /opt/dockerws/scripts/stack.sh site-up app.example.com
```

Los datos escritos después del renombrado quedan en la base nueva.

No se renombran automáticamente:

- **Bases PostgreSQL:** usa `--containers-only`, que cambia solo los contenedores.
- **Bases con rutinas, triggers, vistas o eventos:** guardan el usuario viejo como `DEFINER`.
- **Variables con el nombre de la base distintas de `DB_DATABASE` y `DB_USERNAME`** (por ejemplo, `DB_URL`): el plan las señala, y puedes cambiarlas a mano o usar `--containers-only`.
- **Proyectos con volúmenes y un `compose.yaml` escrito en YAML** en lugar de JSON.

Los respaldos anteriores registran el nombre viejo de la base, así que `restore.sh` no los acepta. Para usarlos, cárgalos con `stack.sh db-import SITIO ruta/database.sql.gz --confirm-overwrite`.
