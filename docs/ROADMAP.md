# Hoja de ruta

## Incluido

- Asistente Bash interactivo y modo no interactivo.
- WordPress, Laravel, PHP y contenido estático.
- MySQL, MariaDB y PostgreSQL dedicados o compartidos en el host; SQLite y sin BD.
- Base y usuario distintos por aplicación, credenciales privadas y aislamiento de redes.
- Certbot en el host, HTTP bootstrap y recarga Nginx tras renovar.
- Backups por motor, cron, retención, checksum y restauración con comprobaciones.
- Pruebas de generación y de integración con Docker.
- Overlays `compose.d/` para adoptar mejoras sin reescribir `compose.yaml`.
- Laravel: `site-composer` sin capabilities, scheduler como servicio, `deploy.sh` con respaldo previo y ganchos, `app.env` y `env-reload`.
- Usuario de despliegue con sudo limitado a `deploy.sh`.
- Releases atómicos opcionales: `storage/` compartido, migraciones antes de activar, `--rollback` con comprobación de migraciones, mirror git y assets con Node.
- `issue-cert.sh --check` (HTTP-01 sin staging) y correo opcional; `proxy-up` sin recrear el proxy; `db-import`; `stack.sh status`.

## Próximas mejoras

1. Workers de colas (`queue:work`) con el mismo patrón que el scheduler (`workers.py`), `queue:restart` en el despliegue, Redis y cron de WordPress.
2. Copias remotas cifradas, retención externa y alertas verificables.
3. Flujo explícito de rotación de credenciales y eliminación de un sitio con salvaguardas.
4. Migración asistida/adopción de bases existentes y traslado entre modalidades.
5. Pruebas completas de Debian 12/13 en máquinas virtuales, incluyendo instalación de motores compartidos, certbot.timer y reinicios del host.
6. Fijación de imágenes por digest, actualización revisada y publicación de imágenes propias.
7. Bases remotas/RDS por TCP con TLS y controles de red; no confundirlas con el modo `host` por socket.
8. Aprovisionamiento opcional de infraestructura AWS mediante IaC, monitoreo de capacidad y restauración integral de servidor.

Agregar perfiles nuevos debe conservar la separación entre datos del servidor y plantillas públicas, y cubrir creación, credenciales, respaldo y restauración.
