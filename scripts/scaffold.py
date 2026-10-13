#!/usr/bin/env python3
"""Backend del asistente Bash. Python stdlib evita dependencias de plantillas/YAML."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from pathlib import Path
from registry import ROOT, settings, sites, validate

LOGGING = {"driver": "local", "options": {"max-size": "10m", "max-file": "5"}}


def write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(content)
    path.chmod(mode)


def bind(source, target, readonly=True):
    return {"type": "bind", "source": source, "target": target, "read_only": readonly,
            "bind": {"create_host_path": False}}


def db_sql(row, password, host="%"):
    db = row["database"]
    name, user = db["name"], db["user"]
    # Identificadores validados, contraseñas hexadecimales generadas internamente.
    if db["engine"] == "postgres":
        return (f"CREATE ROLE {user} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;\n"
                f"CREATE DATABASE {name} OWNER {user};\n"
                f"REVOKE ALL ON DATABASE {name} FROM PUBLIC;\n")
    return (f"CREATE USER '{user}'@'{host}' IDENTIFIED BY '{password}';\n"
            f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{user}'@'{host}';\n")


def compose(row, cfg):
    db = row["database"]
    kind, engine, mode = row["kind"], db["engine"], db["mode"]
    web = {"restart": "unless-stopped", "init": True, "read_only": True,
           "security_opt": ["no-new-privileges:true"], "cap_drop": ["NET_RAW"],
           "mem_limit": cfg["web_memory"], "pids_limit": 128, "logging": LOGGING,
           "networks": {"edge": {"aliases": ["web-" + row["id"]]}},
           "volumes": [bind("./app", "/var/www/html")],
           "tmpfs": ["/tmp:rw,nosuid,size=128m", "/var/run/apache2:rw,nosuid,size=8m",
                     "/var/lock/apache2:rw,nosuid,size=8m"]}
    if kind == "static":
        web.update(image=cfg["nginx_image"], volumes=[bind("./app", "/usr/share/nginx/html"),
                   bind("./nginx.conf", "/etc/nginx/conf.d/default.conf")],
                   tmpfs=["/var/cache/nginx:rw,nosuid,size=32m", "/var/run:rw,nosuid,size=8m"])
    else:
        web.update(build={"context": "../../images/php-apache", "args": {"PHP_IMAGE": f"php:{row['php']}-apache-bookworm"}},
                   env_file=["./runtime.env"], environment={"APP_DOCUMENT_ROOT": "/var/www/html/public" if kind == "laravel" else "/var/www/html"})
        web.update(user="33:33", cap_drop=["ALL"], sysctls={"net.ipv4.ip_unprivileged_port_start": "0"},
                   tmpfs=["/tmp:rw,nosuid,size=128m", "/var/run/apache2:rw,nosuid,uid=33,gid=33,size=8m",
                          "/var/lock/apache2:rw,nosuid,uid=33,gid=33,size=8m"])
        if kind == "laravel":
            web["volumes"] += [bind("./app/storage", "/var/www/html/storage", False),
                               bind("./app/bootstrap/cache", "/var/www/html/bootstrap/cache", False)]
        elif kind == "wordpress":
            web["volumes"].append(bind("./app/wp-content/uploads", "/var/www/html/wp-content/uploads", False))
        else:
            web["volumes"].append(bind("./writable", "/var/www/writable", False))
    result = {"name": "dockerws-" + row["id"], "services": {"web": web},
              "networks": {"edge": {"external": True, "name": "dockerws-edge-" + row["id"]}}}
    if mode == "host":
        web["volumes"].append(bind(db["socket_dir"], "/run/host-db"))
    elif mode == "file":
        web["volumes"].append(bind("./data/sqlite", "/var/lib/app-db", False))
    elif mode == "container":
        result["networks"]["database"] = {"internal": True}
        web["networks"]["database"] = {}
        web["depends_on"] = {"db": {"condition": "service_healthy"}}
        server = {"image": cfg[engine + "_image"], "restart": "unless-stopped",
                  "mem_limit": cfg["db_memory"], "pids_limit": 256, "logging": LOGGING,
                  "security_opt": ["no-new-privileges:true"], "networks": ["database"],
                  "secrets": ["db-root-password"],
                  "volumes": [bind("./secrets/init.sql", "/docker-entrypoint-initdb.d/10-app.sql")],
                  "healthcheck": {"interval": "10s", "timeout": "5s", "retries": 18, "start_period": "30s"}}
        result["volumes"] = {"db-data": {}}
        result["secrets"] = {"db-root-password": {"file": "./secrets/db-root-password"}}
        if engine == "postgres":
            server["environment"] = {"POSTGRES_PASSWORD_FILE": "/run/secrets/db-root-password",
                                     "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"}
            server["volumes"].append("db-data:/var/lib/postgresql/data")
            server["healthcheck"]["test"] = ["CMD-SHELL", f"psql -U postgres -d {db['name']} -Atqc 'SELECT 1' >/dev/null"]
        else:
            prefix = "MARIADB" if engine == "mariadb" else "MYSQL"
            server["environment"] = {prefix + "_ROOT_PASSWORD_FILE": "/run/secrets/db-root-password"}
            server["volumes"].append("db-data:/var/lib/mysql")
            server["secrets"].append({"source": "db-client", "target": "db-client.cnf"})
            result["secrets"]["db-client"] = {"file": "./secrets/db-client.cnf"}
            client = "mariadb" if engine == "mariadb" else "mysql"
            server["healthcheck"]["test"] = ["CMD-SHELL", f"{client} --defaults-extra-file=/run/secrets/db-client.cnf --database={db['name']} -Nse 'SELECT 1' >/dev/null"]
            server["command"] = ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci",
                                 "--innodb-buffer-pool-size=128M", "--max-connections=40"]
        result["services"]["db"] = server
    return result


def scaffold(args):
    cfg = settings()
    digest = hashlib.sha256(args.domain.encode()).hexdigest()[:12]
    ident = args.id or "s" + digest
    engine = args.database
    mode = args.db_mode or ("none" if engine == "none" else "file" if engine == "sqlite" else "container")
    row = {"schema": 1, "id": ident, "domain": args.domain, "aliases": args.alias,
           "kind": args.type, "php": args.php or cfg["php"],
           "database": {"engine": engine, "mode": mode, "name": "dws" + digest, "user": "dws" + digest,
                        "socket_dir": args.socket_dir or ("/var/run/postgresql" if engine == "postgres" else "/run/mysqld"),
                        "socket_name": args.socket_name, "port": args.db_port or (5432 if engine == "postgres" else 3306)}}
    validate(row)
    target = ROOT / "sites" / row["domain"]
    existing = sites()
    names = [row["domain"], *row["aliases"]]
    if len(set(names)) != len(names):
        raise ValueError("Alias duplicado")
    if target.exists() or target.is_symlink() or any(r["id"] == ident or set(names) & {r["domain"], *r["aliases"]} for r in existing):
        raise ValueError("El sitio, su ID o uno de los dominios ya existe; no se sobrescribe")
    staging = Path(tempfile.mkdtemp(prefix=".new-", dir=ROOT / "sites"))
    try:
        for directory in ("app", "secrets", "data", "writable"):
            (staging / directory).mkdir(mode=0o700 if directory == "secrets" else 0o755)
        write(staging / "site.json", json.dumps(row, indent=2) + "\n")
        write(staging / "compose.yaml", json.dumps(compose(row, cfg), indent=2) + "\n")
        write(staging / ".env", "# Reservado para interpolación de Compose. Credenciales en runtime.env.\n")
        password, admin = secrets.token_hex(32), secrets.token_hex(32)
        db = row["database"]
        env = {"APP_ENV": "production", "APP_DEBUG": "false", "APP_URL": "https://" + row["domain"],
               "APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(),
               "LOG_CHANNEL": "stderr", "SESSION_DRIVER": "file", "CACHE_STORE": "file", "QUEUE_CONNECTION": "sync"}
        if engine != "none":
            env.update(DB_CONNECTION={"mariadb": "mysql", "postgres": "pgsql"}.get(engine, engine),
                       DB_HOST=("/run/host-db" if engine == "postgres" else "localhost") if mode == "host" else "db",
                       DB_PORT=str(db["port"]), DB_DATABASE=db["name"], DB_USERNAME=db["user"], DB_PASSWORD=password,
                       DB_SOCKET="/run/host-db/" + db["socket_name"] if mode == "host" and engine != "postgres" else "")
        if engine == "sqlite":
            env = {k: v for k, v in env.items() if not k.startswith("DB_")}
            env.update(DB_CONNECTION="sqlite", DB_DATABASE="/var/lib/app-db/database.sqlite")
            (staging / "data/sqlite").mkdir()
            with sqlite3.connect(staging / "data/sqlite/database.sqlite") as connection:
                connection.execute("PRAGMA user_version=0")
        if args.type == "wordpress":
            env.update(WP_HOME=env["APP_URL"], WP_SITEURL=env["APP_URL"])
            for key in ("AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY", "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT"):
                env["WP_" + key] = secrets.token_hex(48)
            write(staging / "app/wp-config.php", (ROOT / "templates/wordpress/wp-config.php").read_text(), 0o644)
            (staging / "app/wp-content/uploads").mkdir(parents=True)
        elif args.type == "laravel":
            for directory in ("public", "storage/app/public", "storage/framework/cache/data", "storage/framework/sessions", "storage/framework/views", "storage/logs", "bootstrap/cache"):
                (staging / "app" / directory).mkdir(parents=True, exist_ok=True)
        elif args.type == "static":
            write(staging / "nginx.conf", (ROOT / "templates/static/nginx.conf").read_text(), 0o644)
            write(staging / "app/index.html", "<!doctype html><html lang=es><title>Sitio preparado</title><h1>Sitio preparado</h1></html>\n", 0o644)
        else:
            write(staging / "app/index.php", "<?php echo 'Sitio preparado';\n", 0o644)
        write(staging / "runtime.env", "".join(f"{k}={v}\n" for k, v in env.items()))
        if mode in ("host", "container"):
            # El directorio 0700 protege estos archivos en el host; son legibles por el UID de cada imagen al montarlos.
            write(staging / "secrets/db-password", password + "\n")
            write(staging / "secrets/init.sql", db_sql(row, password, "localhost" if mode == "host" else "%"), 0o444)
            if mode == "container":
                write(staging / "secrets/db-root-password", admin + "\n", 0o444)
                write(staging / "secrets/db-client.cnf", f"[client]\nuser=root\npassword={admin}\nprotocol=socket\n", 0o400)
        for path in [staging / "app", *(staging / "app").rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)
        if os.geteuid() == 0:
            writable = [staging / "writable"]
            if engine == "sqlite":
                writable.append(staging / "data/sqlite")
            if args.type == "laravel":
                writable.extend([staging / "app/storage", staging / "app/bootstrap/cache"])
            if args.type == "wordpress":
                writable.append(staging / "app/wp-content/uploads")
            for folder in writable:
                for path in [folder, *folder.rglob("*")]:
                    os.chown(path, 33, 33)
                    path.chmod(0o750 if path.is_dir() else 0o640)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging)
        raise
    print(f"Creado: {target}\nTipo: {args.type}; base de datos: {engine}/{mode}")
    print("Las credenciales están en runtime.env y secrets/; no se imprimen ni se versionan.")
    print(f"Siguientes pasos: importar la aplicación; scripts/stack.sh site-up {args.domain}; scripts/stack.sh proxy-up")


def parser():
    p = argparse.ArgumentParser(description="Crear un sitio sin iniciar servicios ni descargar aplicaciones")
    p.add_argument("--domain", required=True)
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--id")
    p.add_argument("--type", required=True, choices=["wordpress", "laravel", "php", "static"])
    p.add_argument("--php")
    p.add_argument("--database", default="none", choices=["none", "mysql", "mariadb", "postgres", "sqlite"])
    p.add_argument("--db-mode", choices=["container", "host", "file", "none"])
    p.add_argument("--socket-dir")
    p.add_argument("--socket-name", default="mysqld.sock")
    p.add_argument("--db-port", type=int)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    os.umask(0o077)
    try:
        (ROOT / "sites").mkdir(exist_ok=True)
        with (ROOT / "sites/.create.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            scaffold(args)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
