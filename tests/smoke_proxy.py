#!/usr/bin/env python3
"""Nginx real en puertos efímeros de loopback: ACME, TLS y backend estático."""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def request(port, path="/", tls=False):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        conn = ssl._create_unverified_context().wrap_socket(raw, server_hostname="static.example.test") if tls else raw
        conn.sendall(f"GET {path} HTTP/1.0\r\nHost: static.example.test\r\n\r\n".encode())
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
        return data


def main():
    token = "t" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="dockerws-proxy-test-") as temporary:
        root = Path(temporary)
        for name in ("scripts", "config", "templates", "proxy"):
            shutil.copytree(PROJECT / name, root / name, ignore=shutil.ignore_patterns("__pycache__", "local.json", "compose.generated.yaml", "*.conf.tmp"))
        (root / "sites").mkdir()
        run([sys.executable, str(root / "scripts/scaffold.py"), "--domain", "static.example.test", "--type", "static", "--id", token])
        network = "dockerws-edge-" + token
        path = root / "sites/static.example.test"
        site = ["docker", "compose", "--project-directory", str(path), "-f", str(path / "compose.yaml")]
        proxy_path = root / "proxy/compose.generated.yaml"
        proxy = ["docker", "compose", "--project-directory", str(root / "proxy"), "-f", str(proxy_path)]
        certs = root / "certs"
        certs.mkdir()
        acme = root / "acme"
        challenge = acme / ".well-known/acme-challenge/token"
        challenge.parent.mkdir(parents=True)
        challenge.write_text("ACME-OK")
        env = dict(os.environ, LETSENCRYPT_DIR=str(certs))
        run([sys.executable, str(root / "scripts/render-compose.py")])
        run([sys.executable, str(root / "scripts/render-proxy.py")], env=env)
        config = json.loads(proxy_path.read_text())
        config["name"] = "dockerws-proxy-" + token
        config["services"]["proxy"]["ports"] = ["127.0.0.1::80", "127.0.0.1::443"]
        for volume in config["services"]["proxy"]["volumes"]:
            if volume["target"] == "/etc/letsencrypt":
                volume["source"] = str(certs)
            elif volume["target"] == "/var/www/certbot":
                volume["source"] = str(acme)
        proxy_path.write_text(json.dumps(config))
        try:
            run(["docker", "network", "create", network], stdout=subprocess.DEVNULL)
            run(site + ["up", "-d", "--wait", "--wait-timeout", "60"])
            run(proxy + ["up", "-d", "--wait", "--wait-timeout", "60"])
            def port(number):
                return int(run(proxy + ["port", "proxy", str(number)], capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1])
            http_port, tls_port = port(80), port(443)
            assert b"503 Service" in request(http_port)
            assert b"ACME-OK" in request(http_port, "/.well-known/acme-challenge/token")
            live = certs / "live/static.example.test"
            live.mkdir(parents=True)
            run(["openssl", "req", "-config", "/dev/null", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=static.example.test", "-keyout", str(live / "privkey.pem"), "-out", str(live / "fullchain.pem")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run([sys.executable, str(root / "scripts/render-proxy.py")], env=env)
            run(proxy + ["exec", "-T", "proxy", "nginx", "-t"])
            run(proxy + ["exec", "-T", "proxy", "nginx", "-s", "reload"])
            # Esperar la transición de workers mediante solicitudes, con un límite breve.
            import time
            for _ in range(30):
                if b"301 Moved" in request(http_port):
                    break
                time.sleep(0.1)
            assert b"301 Moved" in request(http_port)
            assert b"Sitio preparado" in request(tls_port, tls=True)
            assert b"404 Not Found" in request(tls_port, "/.env", tls=True)
            print("OK proxy: bootstrap, desafío ACME, redirección, TLS y backend estático")
        finally:
            subprocess.run(proxy + ["down", "--remove-orphans"], stdout=subprocess.DEVNULL)
            subprocess.run(site + ["down", "--remove-orphans"], stdout=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
