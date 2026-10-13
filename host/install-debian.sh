#!/usr/bin/env bash
# Ejecutar en el servidor Debian que alojará los sitios.
set -Eeuo pipefail
(( EUID == 0 )) || { echo 'Ejecutar con sudo' >&2; exit 1; }
source /etc/os-release
[[ $ID == debian && ( $VERSION_ID == 13 || $VERSION_ID == 12 ) ]] || { echo 'Se requiere Debian 12 o 13.' >&2; exit 1; }
for package in docker.io docker-compose docker-doc podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
        echo "Paquete incompatible con Docker CE: $package. Revisar la migración de Docker antes de continuar." >&2
        exit 1
    fi
done
apt-get update
apt-get install -y ca-certificates curl python3 certbot cron logrotate rsync gzip util-linux
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $VERSION_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker cron certbot.timer
echo 'Docker, Compose, Certbot y cron instalados. Se conserva la zona horaria del host.'
