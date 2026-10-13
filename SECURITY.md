# Seguridad

DockerWS está destinado a aplicaciones de una misma administración. Los contenedores comparten kernel y el administrador de Docker controla el host. No es una plataforma de aislamiento para clientes hostiles.

- Solo el proxy publica puertos. Cada aplicación tiene red frontal distinta; las BD dedicadas tienen red interna.
- PHP/Apache usa UID 33, filesystem raíz y código RO, capabilities eliminadas y `no-new-privileges`. El modo host expone solamente el socket de BD a esa aplicación; no el sistema de archivos del servidor ni su socket Docker.
- Credenciales únicas de aplicación, sin acceso global ni cuenta administrativa dentro de web.
- Las reglas PostgreSQL generadas protegen las cuentas creadas por DockerWS. Audita roles, grants `PUBLIC` y reglas de otras aplicaciones que ya existan en un motor compartido.
- Los secretos de Compose local son archivos montados, no un almacén cifrado. El administrador del host/Docker puede leerlos, igual que las variables del contenedor. Protege accesos, disco y copias externas.
- El checkout y los scripts que cron ejecuta como root deben ser modificables solo por administradores de confianza. No despliegues contenido de usuarios dentro del propio checkout de herramientas.
- No se admiten PHP 7.4 ni otras ramas EOL en la configuración inicial. Actualiza dependencias y verifica las [versiones soportadas de PHP](https://www.php.net/supported-versions.php).
- Los checksums de backup detectan corrupción; no son firmas. No restaures dumps de origen desconocido como administrador.

Si encuentras una vulnerabilidad, usa el canal privado de seguridad del repositorio cuando esté habilitado. No publiques credenciales, dumps ni detalles explotables de instalaciones reales en un issue público. Esta plantilla no configura por sí misma ese canal de GitHub.
