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

Todos los comandos de DockerWS (`stack.sh`, `backup.sh`, `restore.sh`) cargan `compose.yaml` y después, en orden alfabético, los archivos `compose.d/*.yaml`. Un overlay puede agregar servicios o cambiar un montaje: los volúmenes con el mismo destino se reemplazan y `env_file` se agrega al final. Para ver el resultado combinado:

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
