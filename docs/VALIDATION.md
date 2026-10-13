# Validación

Validación local de la refactorización, 12 de octubre de 2026. Estos resultados corresponden a pruebas locales; no sustituyen la aceptación en un Debian recién instalado ni las pruebas de tus aplicaciones.

## Automatización

`bash tests/check.sh`:

- Sintaxis de los scripts Bash.
- 16 pruebas unitarias y de configuración, incluidas 21 combinaciones de tipo de sitio, motor y modalidad validadas con Docker Compose.
- Rechazo de dominios/alias/ID duplicados, entradas inválidas y combinaciones incompatibles.
- Separación de credenciales, permisos de archivos y cuentas de aplicación sin privilegios globales.
- Protección frente a sobrescritura de recursos del host; orden, recarga y rollback de HBA con clientes simulados.
- Dump fallido o vacío, conservación de copias anteriores, retención selectiva y rechazo de restauración con sitio incorrecto o web encendido.
- SQLite con WAL abierto, checksum y detección de corrupción.
- Configuración Nginx antes/después del certificado, preservando archivos manuales.

También se valida el Compose del proxy con inventario vacío.

## Docker real

`python3 tests/smoke.py`:

- Construcción de la imagen PHP 8.4 con controladores PDO MySQL, PostgreSQL y SQLite.
- MySQL 8.4, MariaDB 11.4 y PostgreSQL 17: volumen nuevo, cuenta de aplicación, conexión PDO y denegación de creación de otra base.
- Arranque de Apache como UID 33 y respuesta HTTP real.
- Inserción de una fila, respaldo, eliminación, restauración y comprobación de su contenido en los tres motores.
- Otro sitio conectado al mismo motor por socket Unix, incluyendo contraseña SCRAM en PostgreSQL.
- Denegación de lectura entre bases en MySQL/MariaDB.
- Proxy Nginx con puertos efímeros de loopback: bootstrap HTTP 503, desafío ACME, redirección HTTP, certificado de laboratorio, HTTPS al backend estático y bloqueo de `.env`.

Los motores compartidos se simulan con contenedores que exponen sus sockets en directorios temporales. Esta prueba verifica transporte y autenticación de la aplicación; **no instala motores ni ejecuta el aprovisionador administrativo sobre un host Debian real**. Ese aprovisionador tiene cobertura unitaria y debe ensayarse en el servidor de pruebas con sus paquetes y autenticación administrativa reales.

## Validación pendiente en el destino

- Instaladores Debian/cron/systemd y reinicio del host.
- Clientes administrativos, permisos de sockets y pg_hba de motores instalados en Debian.
- Emisión/renovación real de Let's Encrypt con DNS público.
- Código completo de tus WordPress/Laravel, plugins, migraciones, correo y servicios externos.
- Construcción de las variantes opcionales PHP 8.3 y 8.5; la integración local usa PHP 8.4.
- Carga, memoria, capacidad de disco, recuperación integral y copia externa.

Las pruebas eliminan solo contenedores, volúmenes, redes y tags temporales propios. Las imágenes base descargadas y la caché de compilación pueden permanecer en Docker.
