#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/../scripts/lib.sh"
require_root
# Las rutas se incrustan en archivos que ejecutará root: limitar su sintaxis.
[[ $DOCKERWS_ROOT =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'La ruta del proyecto no admite espacios ni metacaracteres.' >&2; exit 1; }
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -d -m 0700 /var/backups/dockerws
cat >/etc/letsencrypt/renewal-hooks/deploy/50-dockerws <<EOF
#!/bin/bash
exec "$DOCKERWS_ROOT/scripts/reload-proxy.sh"
EOF
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/50-dockerws
cat >/etc/cron.d/dockerws-backup <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=root
# Hora local del servidor. Configurar un MTA/monitor para recibir fallos.
15 2 * * * root "$DOCKERWS_ROOT/scripts/backup.sh" >>/var/log/dockerws-backup.log 2>&1 || { echo 'Falló DockerWS backup; revisar /var/log/dockerws-backup.log' >&2; exit 1; }
EOF
chmod 0644 /etc/cron.d/dockerws-backup
cat >/etc/logrotate.d/dockerws-backup <<'EOF'
/var/log/dockerws-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    create 0600 root root
}
EOF
touch /var/log/dockerws-backup.log
chmod 0600 /var/log/dockerws-backup.log
systemctl enable --now cron certbot.timer
echo 'Respaldo diario a las 02:15, renovación con certbot.timer y deploy-hook instalados.'
