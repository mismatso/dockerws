#!/usr/bin/env python3
"""Conecta el proxy solamente a las redes de los sitios registrados."""
import json
import os
from registry import ROOT, settings, sites
from scaffold import LOGGING, bind

networks = {"edge-" + row["id"]: {"external": True, "name": "dockerws-edge-" + row["id"]} for row in sites()}
proxy = {"image": settings()["nginx_image"], "restart": "unless-stopped", "init": True,
         "ports": ["80:80", "443:443"], "read_only": True,
         "volumes": [bind("./nginx.conf", "/etc/nginx/nginx.conf"), bind("./conf.d", "/etc/nginx/conf.d"),
                     bind("/etc/letsencrypt", "/etc/letsencrypt"), bind("/var/lib/dockerws/acme", "/var/www/certbot")],
         "tmpfs": ["/var/cache/nginx:rw,nosuid,size=64m", "/var/run:rw,nosuid,size=8m", "/tmp:rw,nosuid,size=16m"],
         "security_opt": ["no-new-privileges:true"], "cap_drop": ["NET_RAW"],
         "mem_limit": "256m", "pids_limit": 128, "logging": LOGGING,
         "ulimits": {"nofile": {"soft": 65536, "hard": 65536}},
         "healthcheck": {"test": ["CMD", "nginx", "-t"], "interval": "30s", "timeout": "5s", "retries": 3}}
if networks:
    proxy["networks"] = list(networks)
result = {"name": "dockerws-proxy", "services": {"proxy": proxy}, "networks": networks}
temp = ROOT / "proxy/.compose.tmp"
temp.write_text(json.dumps(result, indent=2) + "\n")
os.replace(temp, ROOT / "proxy/compose.generated.yaml")
