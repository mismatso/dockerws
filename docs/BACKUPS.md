# Respaldos y recuperación

## Programación y contenido

`host/install-automation.sh` instala `/etc/cron.d/dockerws-backup`: cada día a las 02:15, hora local del host, ejecuta `scripts/backup.sh`. La renovación TLS usa el timer de Certbot, no ese cron.

```bash
sudo ./scripts/backup.sh                       # Todos los sitios con BD
sudo ./scripts/backup.sh app.example.com       # Un sitio
```

La estructura es:

```text
/var/backups/dockerws/<dominio>/<UTC>-<id>/
  database.sql.gz     # MySQL, MariaDB, PostgreSQL
  database.sqlite     # SQLite; uno u otro formato
  manifest.json       # Sitio, motor, base, fecha y SHA-256
```

Cada conjunto se publica mediante un renombrado atómico después de verificar el contenido. Si falla un motor, el comando sigue con los demás y termina con error. Un lock evita respaldos o restauraciones simultáneos. Un respaldo vacío, incompleto o con error del cliente nunca reemplaza una copia anterior.

Retención: 30 días por defecto, aplicada **solo después de un respaldo correcto de ese sitio**. Solo elimina conjuntos con formato y manifiesto válidos; no borra directorios manuales. Los directorios son 0700 y los archivos 0600. Por defecto la operación tiene un límite de 1800 segundos por sitio.

Puedes crear `config/local.json`:

```json
{
  "backup_dir": "/var/backups/dockerws",
  "retention_days": 30,
  "backup_timeout": 1800
}
```

También se admiten `BACKUP_DIR`, `RETENTION_DAYS` y `BACKUP_TIMEOUT` para una ejecución. El directorio debe pertenecer a root, tener permisos 0700 y no ser un enlace simbólico.

### Consistencia

- **MySQL/MariaDB:** dump lógico por base. InnoDB usa `--single-transaction`; si encuentra tablas de otros motores, usa `--lock-all-tables` y registra que habrá un bloqueo. En una instancia compartida ese bloqueo puede afectar a otros sitios. Evita cambios DDL durante el dump. Incluye triggers, rutinas y eventos; utiliza el cliente del motor correspondiente.
- **PostgreSQL:** `pg_dump` de la base con snapshot consistente, sin propietarios ni permisos globales. Al restaurar, los objetos se crean con el rol de la aplicación. No incluye roles/globales del clúster. [Documentación de pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html).
- **SQLite:** API de backup, que incluye correctamente el estado de WAL y verifica `quick_check`. No se copia en crudo un archivo abierto. [API de backup de SQLite](https://www.sqlite.org/backup.html).

Las bases dedicadas usan el cliente dentro de su contenedor; las compartidas usan clientes del host y acceso administrativo local por socket. Para MySQL/MariaDB puede ser necesario el archivo administrativo indicado al aprovisionar.

## Restaurar

Prueba la restauración periódicamente en un entorno aislado. El checksum detecta corrupción, no demuestra que la aplicación funcione ni autentica a quien creó el dump. Usa únicamente respaldos de confianza: contienen SQL que ejecutará el administrador del motor.

1. Conserva una copia del estado actual y detén todos los escritores (web, workers, scheduler y clientes externos).
2. Mantén iniciada la base dedicada. Para un entorno recién creado: `stack.sh db-up DOMINIO`.
3. Restaura con confirmación explícita:

   ```bash
   sudo ./scripts/stack.sh site app.example.com stop web
   sudo ./scripts/restore.sh app.example.com \
     /var/backups/dockerws/app.example.com/20261013T021500Z-1234abcd \
     --confirm-overwrite
   ```

4. Verifica datos y comportamiento antes de reiniciar web:

   ```bash
   sudo ./scripts/stack.sh site-up app.example.com
   ```

El restaurador comprueba dominio, nombre de base, motor, checksum y que no queden servicios Compose distintos de `db` en ejecución. No puede detectar escritores externos al Compose. Una restauración MySQL/MariaDB puede dejar cambios parciales si falla; mantén la aplicación detenida hasta resolverlos.

**PostgreSQL requiere una base vacía.** El dump no elimina objetos existentes y la restauración usa una transacción. Para recuperación sobre un sitio que ya tuvo datos, prepara una base vacía con el mismo propietario, conservando sus restricciones a `PUBLIC`, mediante el administrador del clúster. Esa eliminación es deliberadamente manual. Alternativamente, recupera primero en un servidor aislado con el mismo dominio y metadatos. No uses indiscriminadamente `down -v` en producción.

**SQLite** restaura mediante la API al archivo destino y conserva propietario 33:33. Requiere detener todas sus conexiones escritoras antes de hacerlo.

Importar dumps antiguos o externos es una operación de migración distinta: no tendrán el manifiesto de DockerWS. Utiliza el cliente específico del motor, revisa collation, versión, extensiones, definers, eventos, roles y permisos. En particular, los dumps recientes de MariaDB incluyen comandos que el cliente de MySQL no interpreta; [compatibilidad de mariadb-dump](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-clients/mariadb-dump).

## Qué más debes respaldar

Estos scripts respaldan **bases de datos**. Para recuperación completa también necesitas:

- Código, `wp-content/uploads`, Laravel storage y otros archivos de aplicación.
- `sites/`, incluyendo `runtime.env`, `secrets/`, Compose y metadatos, protegidos y cifrados.
- Claves de aplicación originales, configuración del host y `/etc/letsencrypt`.
- Extensiones, roles y configuración de los motores compartidos que administres fuera de DockerWS.

Una copia en el mismo disco no protege frente a pérdida del servidor o del volumen. Copia los conjuntos a un destino externo cifrado (por ejemplo S3 con retención/versionado) y prueba su recuperación. Esta versión no configura credenciales cloud ni sube archivos automáticamente.

## Observar fallos

Logs: `/var/log/dockerws-backup.log`, con logrotate. El cron usa `MAILTO=root` y emite un aviso en caso de error, pero solo recibirás correo si configuras un MTA y entrega externa. Integra el código de salida con tu sistema de monitoreo y alerta si no aparece una copia reciente o falta espacio. Certbot también requiere supervisión; comprueba el timer y ensaya `certbot renew --dry-run`.
