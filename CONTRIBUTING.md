# Contribuir

Mantén ejemplos con dominios reservados `example.com`/`example.test`. Nunca añadas instancias reales bajo `sites/`, secretos, archivos SQL, certificados o dumps al repositorio.

Ejecuta `bash tests/check.sh` para sintaxis Bash, validaciones de configuración y pruebas de automatización. Con la CLI de Compose instalada se valida toda la matriz de plantillas sin iniciar un daemon. Ejecuta también `python3 tests/smoke.py` cuando cambies imágenes, controladores, autenticación o comandos de respaldo/restauración; necesita Docker y acceso a los registros de imágenes.

Los generadores utilizan Python estándar para JSON y archivos, y Bash para la interfaz operativa. Evita `eval`, secretos por línea de comandos y evaluaciones shell de entradas del usuario. Un sitio existente no debe sobrescribirse silenciosamente.

Incluye documentación y pruebas del comportamiento que cambias. La compatibilidad declarada con un motor incluye crear una cuenta sin privilegios globales, conectar, respaldar y recuperar datos. Los cambios destructivos o de versión mayor requieren un procedimiento de migración explícito.
