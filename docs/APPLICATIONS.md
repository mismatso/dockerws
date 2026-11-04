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

Revisa `runtime.env`: configura correo, servicios externos y variables propias. En una migración **conserva el APP_KEY original** para poder descifrar datos existentes. El generador crea una clave nueva solo como valor inicial; no ejecutes `key:generate` sobre una aplicación migrada. Retira del código cualquier `.env` anterior para no mezclar configuraciones.

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
