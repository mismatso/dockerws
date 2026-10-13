# Hoja de ruta

## Incluido

- Asistente Bash interactivo y modo no interactivo.
- WordPress, Laravel, PHP y contenido estático.
- MySQL, MariaDB y PostgreSQL dedicados o compartidos en el host; SQLite y sin BD.
- Base y usuario distintos por aplicación, credenciales privadas y aislamiento de redes.
- Certbot en el host, HTTP bootstrap y recarga Nginx tras renovar.
- Backups por motor, cron, retención, checksum y restauración con comprobaciones.
- Pruebas de generación y de integración con Docker.

## Próximas mejoras

1. Copias remotas cifradas, retención externa y alertas verificables.
2. Perfiles de workers, scheduler y Redis para Laravel, y cron de WordPress.
3. Flujo explícito de rotación de credenciales y eliminación de un sitio con salvaguardas.
4. Migración asistida/adopción de bases existentes y traslado entre modalidades.
5. Pruebas completas de Debian 12/13 en máquinas virtuales, incluyendo instalación de motores compartidos, certbot.timer y reinicios del host.
6. Fijación de imágenes por digest, actualización revisada y publicación de imágenes propias.
7. Bases remotas/RDS por TCP con TLS y controles de red; no confundirlas con el modo `host` por socket.
8. Aprovisionamiento opcional de infraestructura AWS mediante IaC, monitoreo de capacidad y restauración integral de servidor.

Agregar perfiles nuevos debe conservar la separación entre datos del servidor y plantillas públicas, y cubrir creación, credenciales, respaldo y restauración.
