# Bases de datos

## Elegir modalidad

| Modalidad | Ventajas | Coste operativo |
| --- | --- | --- |
| `container` | Versión, recursos y ciclo de vida propios por sitio | Un proceso servidor y su memoria base por aplicación |
| `host` | Una instancia sirve a varios sitios; menor memoria base | Actualizaciones, capacidad y disponibilidad compartidas |
| `file` (SQLite) | Sin proceso servidor; archivo por sitio | Adecuado solo si la aplicación y su concurrencia lo permiten |
| `none` | Sin dependencia de BD | La aplicación debe soportarlo |

Compartir una **instancia** no significa compartir una base ni una cuenta. DockerWS genera identificadores `dws<hash>` y contraseñas aleatorias de 256 bits por sitio. MySQL/MariaDB reciben permisos sobre esa base solamente, sin `GRANT OPTION`. PostgreSQL usa un rol sin `SUPERUSER`, `CREATEDB` ni `CREATEROLE`; la base pertenece a ese rol y revoca acceso a `PUBLIC`.

## Contenedor dedicado

La imagen oficial inicializa el volumen vacío con la cuenta administrativa y luego ejecuta `secrets/init.sql`, que crea la cuenta de aplicación. La cuenta de PostgreSQL de la aplicación **no** se utiliza como `POSTGRES_USER`, ya que esa variable crea un superusuario. [Imagen oficial de PostgreSQL](https://hub.docker.com/_/postgres).

Solo web y db comparten la red interna. El proxy no accede a esa red. La aplicación recibe únicamente su contraseña; los secretos administrativos solo se montan en db. El directorio `secrets/` tiene permisos 0700. Algunos archivos internos son legibles para permitir que las imágenes los lean al montarlos bajo su UID; su directorio padre impide leerlos a usuarios locales ordinarios.

Los scripts de inicialización solo se ejecutan sobre un volumen vacío. Editar contraseñas en archivos después de arrancar no las rota en el motor. Tampoco basta con cambiar la etiqueta de una versión mayor de MySQL/PostgreSQL/MariaDB: respalda, prueba la migración compatible y planifica el cambio.

## Instancia instalada en Debian

La primera versión utiliza **sockets Unix**, sin TCP entre aplicación y host. El contenedor monta el directorio del socket de solo lectura, no el directorio de datos. PDO soporta esta conexión tanto para [MySQL](https://www.php.net/manual/en/ref.pdo-mysql.connection.php) como para [PostgreSQL](https://www.php.net/manual/en/ref.pdo-pgsql.connection.php).

Apache/PHP corre como UID 33, sin capabilities ni posibilidad de elevar privilegios. Esto también evita que el proceso web se presente como el usuario Unix root ante la autenticación local del motor. No cambies a root el servicio web ni agregues el socket de Docker a un contenedor.

Instala el motor que hayas elegido mediante sus paquetes compatibles con tu Debian. Por ejemplo:

```bash
# Elegir uno; no es necesario instalar ambos.
sudo apt install mariadb-server mariadb-client
# O bien:
sudo apt install postgresql postgresql-client
```

Para Oracle MySQL usa su distribución/repositorio oficial compatible con Debian y sus clientes `mysql`/`mysqldump`. Los paquetes MySQL predeterminados de Debian pueden corresponder a MariaDB: no confundas el motor ni los clientes. En PostgreSQL usa `pg_dump` de la misma versión mayor del servidor o una versión compatible más nueva.

El servidor debe estar iniciado, con socket accesible a UID 33. Mantén la escucha TCP limitada a loopback o deshabilitada según tus otras aplicaciones; DockerWS no cambia `bind-address` ni `listen_addresses` y no abre puertos del host.

### MySQL y MariaDB

Valores predeterminados: directorio `/run/mysqld`, archivo `mysqld.sock`. Se pueden cambiar en el asistente. El usuario de aplicación se crea como `'usuario'@'localhost'`, apropiado para el socket.

```bash
sudo ./scripts/provision-host-db.sh app.example.com
```

El script intenta administrar por socket como root del motor. Si tu instalación exige contraseña administrativa, prepara un archivo fuera del repositorio, propiedad de root y modo 0600:

```ini
[client]
user=root
password=TU_PASSWORD_ADMINISTRATIVO
```

```bash
sudo ./scripts/provision-host-db.sh app.example.com --admin-cnf /root/mysql-admin.cnf
```

Solo se guarda la ruta en `host-admin.json`; los respaldos reutilizan ese archivo. No pases contraseñas por argumentos ni pegues el archivo en tickets. Los permisos por base siguen el modelo de [cuentas de MySQL](https://dev.mysql.com/doc/refman/8.4/en/create-user.html) y [GRANT de MariaDB](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/grant).

### PostgreSQL

Valores predeterminados: directorio `/var/run/postgresql`, clúster con puerto 5432. El puerto identifica el archivo `.s.PGSQL.5432`; no implica publicar TCP.

La administración ejecuta `psql` como usuario Unix `postgres`, usando autenticación `peer`. El script crea base y rol, guarda una copia privada del `pg_hba.conf` anterior en `secrets/pg_hba.before`, agrega al principio reglas para la cuenta generada y recarga PostgreSQL. No reinicia el servicio ni modifica reglas de otras cuentas.

Las reglas permiten a esa cuenta autenticar con `scram-sha-256` en su base por socket y rechazan sus conexiones a otras bases o por TCP. La prioridad importa: PostgreSQL utiliza la primera regla aplicable. El administrador local `postgres` debe conservar su acceso. [Formato y orden de pg_hba.conf](https://www.postgresql.org/docs/current/auth-pg-hba-conf.html).

## Fallos parciales y repetición

La creación no adopta bases o cuentas existentes y no cambia sus contraseñas. Una segunda ejecución con `host-db.ready` conserva todo. Si una operación falla después de crear un recurso, se deja para diagnóstico y se bloquea la repetición automática: borrar por cuenta propia podría destruir datos.

Revisa como administrador qué se creó y el SQL privado `secrets/init.sql`. Si confirmas que son recursos **nuevos, vacíos y exclusivos de ese intento**, elimina manualmente esos recursos y las reglas HBA de esa cuenta antes de reintentar. También puedes completar manualmente las operaciones pendientes y crear el marcador `host-db.ready` después de verificar la autenticación y permisos. No copies el SQL completo en logs públicos: incluye una contraseña.

No se incluye eliminación automática de bases, rotación de credenciales ni adopción de bases existentes. Para migrar un sitio, crea un destino nuevo e importa su dump; para compartir una base existente entre aplicaciones hay que diseñar sus permisos expresamente fuera de este flujo.

## SQLite

Archivo: `sites/DOMINIO/data/sqlite/database.sqlite`, montado como `/var/lib/app-db/database.sqlite`. El directorio completo se monta para permitir WAL y journals. Para Laravel se genera `DB_CONNECTION=sqlite` y `DB_DATABASE` con esa ruta. SQLite no está disponible para WordPress en esta plantilla.
