# Preparar aplicaciones

Los ejemplos asumen que ya creaste el sitio con el asistente. No reemplaces `runtime.env` con un `.env` de desarrollo, y conserva sus credenciales al importar código.

## PHP

Copia el código a `sites/DOMINIO/app/`. La raíz web es `/var/www/html`. Están instalados `pdo_mysql`, `mysqli`, `pdo_pgsql`, `pdo_sqlite`, `mbstring`, `intl`, `zip`, `gd`, `bcmath` y OPcache. PDO recibe los parámetros mediante `getenv('DB_*')`; adapta tu aplicación para usarlos. Para MySQL del host usa `DB_SOCKET` en el DSN, no una conexión TCP a `localhost`.

El código es de solo lectura. Para archivos modificables utiliza `/var/www/writable` (persistente en `sites/DOMINIO/writable`) o agrega montajes explícitos al Compose. Mantén directorios legibles por UID 33 y archivos de código normalmente 0644. No apliques `chmod 777`.

## Laravel

Copia el proyecto completo a `app/`; la raíz web será `app/public`. Incluye `vendor/` construido para la versión de PHP elegida o instala dependencias con la imagen del proyecto. Ejemplo para un código ya importado:

```bash
sudo ./scripts/stack.sh networks
sudo ./scripts/stack.sh site app.example.com build web
sudo ./scripts/stack.sh site app.example.com run --rm --no-deps --user 0:0 \
  -v /opt/dockerws/sites/app.example.com/app:/var/www/html \
  web composer install --no-dev --prefer-dist --optimize-autoloader --no-interaction
```

Ese comando es una tarea administrativa temporal con código escribible: utiliza solamente dependencias de confianza. El servicio web sigue corriendo como UID 33 con código de solo lectura. Puedes construir dependencias y assets en CI en su lugar; Node no forma parte de la imagen PHP.

Revisa `runtime.env`: configura correo, servicios externos y variables propias. En una migración **conserva el APP_KEY original** para poder descifrar datos existentes. El generador crea una clave nueva solo como valor inicial; no ejecutes `key:generate` sobre una aplicación migrada. Retira del código cualquier `.env` anterior para no mezclar configuraciones.

Después de importar, verifica permisos de `app/storage` y `app/bootstrap/cache`: propietario 33:33, directorios 0750 y archivos escribibles por el propietario. El asistente prepara esos directorios, pero rsync o una restauración pueden cambiar sus permisos.

```bash
sudo chown -R 33:33 sites/app.example.com/app/storage sites/app.example.com/app/bootstrap/cache
sudo ./scripts/stack.sh site-up app.example.com
sudo ./scripts/stack.sh site app.example.com exec --user 33:33 web php artisan config:clear
# Cuando hayas revisado los cambios de esquema y respaldado:
sudo ./scripts/stack.sh site app.example.com exec --user 33:33 web php artisan migrate --force
sudo ./scripts/stack.sh site app.example.com exec --user 33:33 web php artisan config:cache
```

Configura `trustProxies` en `bootstrap/app.php` para aceptar los encabezados `X-Forwarded-*` del proxy. En esta topología el único ingreso web publicado es Nginx; puedes usar `at: '*'` mientras mantengas esa restricción. No añadas puertos públicos al backend. [Proxies en Laravel](https://laravel.com/framework/docs/requests).

La configuración inicial usa sesiones y caché en archivos, y cola `sync`. Workers permanentes, Redis y scheduler son extensiones de la hoja de ruta. Si los agregas, dales el mismo entorno y permisos, y detén todos los escritores al restaurar datos. El enlace `public/storage` debe prepararse durante el despliegue; `storage:link` no puede escribir sobre el código montado RO en ejecución.

## WordPress

Descarga el código desde [WordPress.org](https://wordpress.org/download/) o importa tu sitio a `app/`. Conserva/adapta el `wp-config.php` generado, que lee credenciales, URL HTTPS y salts de `runtime.env`.

Si migras un sitio, ajusta `$table_prefix` al prefijo de sus tablas, copia `wp-content` y verifica las URL almacenadas. Para cambiar dominios utiliza una herramienta que respete datos serializados, como WP-CLI, después de respaldar. Si quieres conservar sesiones existentes, conserva también los salts de origen.

Solo `wp-content/uploads` es escribible en ejecución. La instalación y actualización de plugins, temas y core se hace mediante tu proceso de despliegue; el panel no modifica código. Revisa cada plugin por si requiere un directorio de caché escribible y monta solo ese directorio. No hagas todo `app/` escribible para resolver permisos.

`wp-cron` conserva el comportamiento normal de WordPress; el cron del host que instala este proyecto corresponde a respaldos. Puedes sustituir wp-cron por una tarea controlada si tu sitio lo necesita.

## Estáticos

Copia HTML, CSS, JavaScript y assets a `app/`. La plantilla sirve archivos existentes y devuelve 404 para los demás. Para una SPA ajusta `sites/DOMINIO/nginx.conf` a `try_files $uri $uri/ /index.html;`. Los archivos generados pertenecen a tu instalación y se pueden editar sin cambiar las plantillas públicas.

## Actualizaciones

Respalda y prueba primero. Para actualizar dentro de las ramas elegidas, descarga/reconstruye y recrea cada sitio durante mantenimiento. No hay un actualizador automático de imágenes ni cambios mayores automáticos. Las bases del host se actualizan con sus procedimientos de Debian/proveedor y afectan a todos los sitios que las usan.
