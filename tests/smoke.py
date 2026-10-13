#!/usr/bin/env python3
"""Integración opt-in con Docker real. Solo elimina recursos con un ID aleatorio propio."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def main():
    if "--proxy-only" in sys.argv:
        run([sys.executable, str(PROJECT / "tests/smoke_proxy.py")])
        return
    token = "t" + uuid.uuid4().hex[:10]
    image = "dockerws-test:" + token
    resources = []
    with tempfile.TemporaryDirectory(prefix="dockerws-smoke-") as temporary:
        root = Path(temporary)
        for name in ("scripts", "config", "templates", "images"):
            shutil.copytree(PROJECT / name, root / name, ignore=shutil.ignore_patterns("__pycache__", "local.json"))
        (root / "sites").mkdir()
        sys.path.insert(0, str(root / "scripts"))
        import registry
        import scaffold
        import database
        import backups
        try:
            print("Construyendo PHP 8.4 con los tres controladores PDO…", flush=True)
            run(["docker", "build", "--quiet", "-t", image, str(root / "images/php-apache")])
            for engine in ("mysql", "mariadb", "postgres"):
                print(f"Probando {engine}: credenciales, PDO, backup y restore…", flush=True)
                domain = engine + ".example.test"
                args = scaffold.parser().parse_args(["--domain", domain, "--id", token + engine,
                                                    "--type", "php", "--database", engine])
                scaffold.scaffold(args)
                row = registry.site(domain)
                path = root / "sites" / domain
                config = json.loads((path / "compose.yaml").read_text())
                config["services"]["web"].pop("build")
                config["services"]["web"]["image"] = image
                # Solo fixture: bind para probar la misma conexión Unix usada en modalidad host.
                socket_dir = path / "socket"
                socket_dir.mkdir(mode=0o777)
                socket_dir.chmod(0o777)
                socket_target = "/var/run/postgresql" if engine == "postgres" else "/var/run/mysqld"
                config["services"]["db"]["volumes"].append(scaffold.bind(str(socket_dir), socket_target, False))
                (path / "compose.yaml").write_text(json.dumps(config))
                network = "dockerws-edge-" + row["id"]
                command = registry.compose_args(row)
                resources.append((command, network))
                run(["docker", "network", "create", network], stdout=subprocess.DEVNULL)
                run(command + ["up", "-d", "--wait", "--wait-timeout", "180", "db"])
                php = '''
$engine = getenv('DB_CONNECTION');
$dsn = $engine === 'pgsql' ? 'pgsql:host='.getenv('DB_HOST').';port='.getenv('DB_PORT').';dbname='.getenv('DB_DATABASE') :
    (getenv('DB_SOCKET') ? 'mysql:unix_socket='.getenv('DB_SOCKET') : 'mysql:host='.getenv('DB_HOST')).';dbname='.getenv('DB_DATABASE');
$db = new PDO($dsn, getenv('DB_USERNAME'), getenv('DB_PASSWORD'), [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$db->exec('CREATE TABLE smoke (id INTEGER PRIMARY KEY)');
$db->exec('INSERT INTO smoke VALUES (42)');
if ((int)$db->query('SELECT id FROM smoke')->fetchColumn() !== 42) exit(2);
try { $db->exec('CREATE DATABASE forbidden'); exit(3); } catch (PDOException $expected) {}
echo "PDO y privilegios: OK\\n";
'''
                run(command + ["run", "--rm", "--no-deps", "-T", "web", "php", "-r", php])
                # Arrancar Apache con el UID no privilegiado y verificar una petición HTTP real.
                run(command + ["up", "-d", "--wait", "--wait-timeout", "60", "web"])
                run(command + ["exec", "-T", "web", "php", "-r",
                               "if (file_get_contents('http://127.0.0.1/') !== 'Sitio preparado') exit(5);"])
                run(command + ["stop", "web"], stdout=subprocess.DEVNULL)
                destination = root / (engine + "-backup")
                destination.mkdir(mode=0o700)
                backups.backup_one(row, destination)
                backups.verify(row, destination)
                database.query(row, "DROP TABLE smoke;", row["database"]["name"])
                backups.restore(argparse.Namespace(domain=domain, folder=str(destination), confirm_overwrite=True))
                assert database.query(row, "SELECT id FROM smoke;", row["database"]["name"]) == "42"
                # Crear otra base/usuario en el mismo servidor y conectar desde un contenedor por socket.
                host_domain = "host-" + domain
                host_args = scaffold.parser().parse_args(["--domain", host_domain, "--id", token + "h" + engine,
                          "--type", "php", "--database", engine, "--db-mode", "host", "--socket-dir", str(socket_dir)])
                scaffold.scaffold(host_args)
                host_row = registry.site(host_domain)
                host_path = root / "sites" / host_domain
                sql = (host_path / "secrets/init.sql").read_text()
                database.query(row, sql)
                host_config = json.loads((host_path / "compose.yaml").read_text())
                host_config["services"]["web"].pop("build")
                host_config["services"]["web"]["image"] = image
                host_network = "dockerws-edge-" + host_row["id"]
                host_command = registry.compose_args(host_row)
                resources.append((host_command, host_network))
                (host_path / "compose.yaml").write_text(json.dumps(host_config))
                run(["docker", "network", "create", host_network], stdout=subprocess.DEVNULL)
                if engine == "postgres":
                    # Simula pg_hba de Debian: contraseña para la cuenta de app, antes de peer/trust.
                    hba = database.query(row, "SHOW hba_file;")
                    rule = f"local {host_row['database']['name']} {host_row['database']['user']} scram-sha-256\n"
                    run(command + ["exec", "-T", "--user", "postgres", "db", "sh", "-c",
                                   't=$(mktemp); cat > "$t"; cat "$1" >> "$t"; cat "$t" > "$1"; rm "$t"', "sh", hba], input=rule, text=True)
                    database.query(row, "SELECT pg_reload_conf();")
                run(host_command + ["run", "--rm", "--no-deps", "-T", "web", "php", "-r", php])
                # Una cuenta de otro sitio no puede leer la tabla de esta base.
                denied = f"try {{ $db->query('SELECT * FROM {row['database']['name']}.smoke'); exit(4); }} catch (PDOException $expected) {{}}" if engine != "postgres" else ""
                if denied:
                    run(host_command + ["run", "--rm", "--no-deps", "-T", "web", "php", "-r", php.split("$db->exec('CREATE TABLE")[0] + denied])
                run(command + ["down", "-v", "--remove-orphans"], stdout=subprocess.DEVNULL)
                print(f"OK {engine}: contenedor dedicado y conexión compartida por socket", flush=True)
        finally:
            for command, network in reversed(resources):
                subprocess.run(command + ["down", "-v", "--remove-orphans"], stdout=subprocess.DEVNULL)
                subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL)
            subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL)
    print("Integración completa: MySQL, MariaDB y PostgreSQL.", flush=True)
    run([sys.executable, str(PROJECT / "tests/smoke_proxy.py")])


if __name__ == "__main__":
    main()
