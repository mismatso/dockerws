#!/usr/bin/env python3
"""Separa las variables de la aplicación (app.env) de las credenciales generadas (runtime.env).

Compose carga app.env después de runtime.env. Este módulo nunca imprime valores.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from registry import ROOT, site
import workers

KEY = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
# Credenciales que genera DockerWS: se quedan en runtime.env.
GENERATED = re.compile(r"(APP_KEY|DB_[A-Z0-9_]+|WP_(AUTH|SECURE_AUTH|LOGGED_IN|NONCE)_(KEY|SALT))")
OVERLAY = "10-app-env.yaml"
APP_ENV = {"path": "./app.env", "required": False}

TEMPLATE = """# Variables propias de la aplicación. Compose carga este archivo DESPUÉS de runtime.env,
# así que una clave repetida aquí reemplaza a la de runtime.env (env-reload lo advierte).
# Las credenciales generadas (APP_KEY, DB_*) permanecen en runtime.env.
# Tras editarlo: sudo ./scripts/stack.sh env-reload DOMINIO
"""
LARAVEL_EXAMPLES = """#
# APP_NAME="Mi aplicación"
# APP_LOCALE=es
# APP_FALLBACK_LOCALE=es
# SESSION_SECURE_COOKIE=true
# MAIL_MAILER=smtp
# MAIL_HOST=smtp.example.com
# MAIL_PORT=587
# MAIL_USERNAME=
# MAIL_PASSWORD=
# MAIL_FROM_ADDRESS=no-reply@example.com
# MAIL_FROM_NAME="Mi aplicación"
"""


def template(kind):
    return TEMPLATE + (LARAVEL_EXAMPLES if kind == "laravel" else "")


def keys(text):
    result = []
    for line in text.splitlines():
        if line.strip().startswith("#") or not line.strip():
            continue
        match = KEY.match(line)
        if not match:
            raise ValueError("Línea sin formato CLAVE=valor (no se muestra su contenido)")
        result.append(match.group(1))
    return result


def site_path(row, name):
    return ROOT / "sites" / row["domain"] / name


def write_private(path, content):
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("x") as stream:
        stream.write(content)
    temp.chmod(0o600)
    os.replace(temp, path)


def ensure_app_env(row):
    path = site_path(row, "app.env")
    if path.is_symlink():
        raise ValueError("app.env no puede ser un enlace simbólico")
    if not path.exists():
        write_private(path, template(row["kind"]))
        return True
    return False


def loads_app_env(env_files):
    return any((e if isinstance(e, str) else e.get("path", "")).endswith("/app.env") or
               (e if isinstance(e, str) else e.get("path", "")) == "app.env" for e in env_files or [])


def adopt(row, service="web", config=None):
    """Hace que web (y el scheduler gestionado) carguen app.env, mediante un overlay."""
    if row["kind"] == "static":
        raise ValueError("Un sitio estático no tiene variables de entorno")
    created = ensure_app_env(row)
    config = config if config is not None else workers.resolved(row)
    services = config.get("services", {})
    if service not in services:
        raise ValueError(f"El Compose no define el servicio {service}")
    changed = []
    if not loads_app_env(services[service].get("env_file")):
        path = site_path(row, "compose.d") / OVERLAY
        if path.exists():
            raise ValueError(f"Ya existe {path.relative_to(ROOT)}")
        workers.write_overlay(path, json.dumps({"services": {service: {"env_file": [APP_ENV]}}}, indent=2) + "\n")
        changed.append(path)
    # El overlay del scheduler lo genera DockerWS: se actualiza en lugar de duplicar servicios.
    scheduler = workers.overlay_path(row, "scheduler")
    if scheduler.exists():
        data = json.loads(scheduler.read_text())
        entry = data["services"]["scheduler"]
        if not loads_app_env(entry.get("env_file")):
            entry["env_file"] = list(entry.get("env_file") or []) + [APP_ENV]
            workers.write_overlay(scheduler, json.dumps(data, indent=2) + "\n")
            changed.append(scheduler)
    return created, changed


def move(row, names):
    """Mueve claves de runtime.env a app.env, con copia previa de runtime.env."""
    runtime, app = site_path(row, "runtime.env"), site_path(row, "app.env")
    if not app.exists():
        raise ValueError("Primero ejecuta env-split sin --move para crear app.env")
    lines = runtime.read_text().splitlines(keepends=True)
    present = keys("".join(lines))
    existing = keys(app.read_text())
    for name in names:
        if GENERATED.fullmatch(name):
            raise ValueError(f"{name} es una credencial generada; se queda en runtime.env")
        if name not in present:
            raise ValueError(f"{name} no está en runtime.env")
        if name in existing:
            raise ValueError(f"{name} ya está en app.env")
    keep, moved = [], []
    for line in lines:
        match = None if line.strip().startswith("#") else KEY.match(line)
        (moved if match and match.group(1) in names else keep).append(line)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = runtime.with_name(f"runtime.env.{stamp}.bak")
    with backup.open("x") as stream:
        stream.write("".join(lines))
    backup.chmod(0o600)
    text = app.read_text()
    write_private(app, text + ("" if text.endswith("\n") or not text else "\n") + "".join(
        line if line.endswith("\n") else line + "\n" for line in moved))
    write_private(runtime, "".join(keep))
    return backup


def overlap(row):
    runtime, app = site_path(row, "runtime.env"), site_path(row, "app.env")
    if not app.exists():
        return []
    return sorted(set(keys(runtime.read_text())) & set(keys(app.read_text())))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = p.add_subparsers(dest="action", required=True)
    s = commands.add_parser("split")
    s.add_argument("domain")
    s.add_argument("--service", default="web")
    s.add_argument("--move", nargs="+", default=[], metavar="CLAVE")
    c = commands.add_parser("check")
    c.add_argument("domain")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    os.umask(0o077)
    row = site(args.domain)
    if args.action == "split":
        created, changed = adopt(row, args.service)
        print("Creado app.env." if created else "app.env ya existía.")
        for path in changed:
            print(f"Actualizado {path.relative_to(ROOT)}")
        if args.move:
            backup = move(row, args.move)
            print(f"Movidas a app.env: {', '.join(args.move)}. Copia previa: {backup.name}")
        print(f"Aplica los cambios con: stack.sh env-reload {row['domain']}")
    else:
        repeated = overlap(row)
        if repeated:
            print("AVISO: app.env reemplaza estas claves de runtime.env: " + ", ".join(repeated), file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc or 'sitio no registrado'}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: docker compose terminó con código {exc.returncode}", file=sys.stderr)
        sys.exit(1)
