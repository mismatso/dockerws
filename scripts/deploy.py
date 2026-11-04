#!/usr/bin/env python3
"""Tareas de aplicación: composer. Se invoca como root desde stack.sh."""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from registry import ROOT, compose_args, site

COMPOSER_CACHE = Path("/var/cache/dockerws/composer")
# Subcomandos que escriben vendor/ o composer.lock: corren como root con app/ escribible.
COMPOSER_WRITE = {"install", "update", "require", "remove", "dump-autoload", "reinstall", "bump"}
# Subcomandos de consulta: corren como 33:33 con el código de solo lectura.
COMPOSER_READ = {"show", "outdated", "validate", "audit", "licenses", "depends", "why", "why-not",
                 "diagnose", "check-platform-reqs", "fund", "suggests"}
COMPOSER_DEFAULT = ["install", "--no-dev", "--prefer-dist", "--optimize-autoloader", "--no-interaction"]
LARAVEL_WRITABLE = ("storage", "bootstrap/cache")


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def build_quiet(row, verbose=False):
    """Construye las imágenes; la salida solo se muestra si falla o con --verbose."""
    command = compose_args(row) + ["build"]
    if verbose:
        run(command)
        return
    with tempfile.TemporaryFile() as log:
        status = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
        if status:
            log.seek(0)
            sys.stderr.buffer.write(log.read())
            raise RuntimeError("Falló la construcción de la imagen")


def app_dir(row):
    path = ROOT / "sites" / row["domain"] / "app"
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{path} no es un directorio")
    return path


def fix_owner(row):
    """Cede a 33:33 los directorios escribibles de Laravel, sin seguir enlaces simbólicos."""
    app = app_dir(row)
    for name in LARAVEL_WRITABLE:
        folder = app / name
        if folder.is_symlink():
            raise ValueError(f"{folder} es un enlace simbólico; no se cambia su propietario")
        folder.mkdir(parents=True, exist_ok=True)
        for current, dirs, files in os.walk(folder):
            for entry in [current, *(os.path.join(current, n) for n in dirs + files)]:
                os.chown(entry, 33, 33, follow_symlinks=False)
                if not os.path.islink(entry):
                    mode = os.stat(entry).st_mode & 0o7777
                    os.chmod(entry, mode | (0o700 if os.path.isdir(entry) else 0o600))


def composer_plan(row, args, service="web"):
    """Devuelve los pasos: ('run', comando) o ('chown', None). Separado para poder probarlo."""
    kind = row["kind"]
    if kind == "static":
        raise ValueError("Un sitio estático no utiliza composer")
    args = list(args) or list(COMPOSER_DEFAULT)
    action = args[0]
    base = compose_args(row) + ["run", "--rm", "--no-deps", "-e", "COMPOSER_HOME=/tmp/composer"]
    if action in COMPOSER_READ:
        return [("run", base + ["--user", "33:33", service, "composer", *args])]
    if action not in COMPOSER_WRITE:
        raise ValueError(f"Subcomando de composer no admitido: {action}")
    app = app_dir(row)
    cache = COMPOSER_CACHE / row["id"]
    # Root del contenedor no tiene DAC_OVERRIDE: escribe app/ (de root) pero no lee storage/ (33:33).
    # Por eso en Laravel los scripts se ejecutan después, como 33:33.
    write = base + ["--user", "0:0", "-e", "COMPOSER_ALLOW_SUPERUSER=1", "-e", "COMPOSER_CACHE_DIR=/composer-cache",
                    "-v", f"{app}:/var/www/html", "-v", f"{cache}:/composer-cache", service, "composer", *args]
    if kind != "laravel":
        return [("cache", cache), ("run", write)]
    if "--no-scripts" not in args:
        write.append("--no-scripts")
    steps = [("cache", cache), ("run", write), ("chown", None)]
    if "--no-scripts" not in args:
        # post-autoload-dump ejecuta package:discover y solo necesita escribir bootstrap/cache.
        steps.append(("run", base + ["--user", "33:33", service, "composer", "run-script",
                                     "post-autoload-dump", "--no-interaction"]))
    return steps


def apply(row, steps):
    for kind, value in steps:
        if kind == "cache":
            value.mkdir(parents=True, mode=0o700, exist_ok=True)
        elif kind == "chown":
            fix_owner(row)
        else:
            run(value)


def composer(row, args, service="web", verbose=False):
    build_quiet(row, verbose)
    # post-autoload-dump de Laravel borra bootstrap/cache/config.php: se regenera si existía.
    cached = row["kind"] == "laravel" and (app_dir(row) / "bootstrap/cache/config.php").exists()
    steps = composer_plan(row, args, service)
    apply(row, steps)
    if cached and steps[-1][1][-3:-1] == ["run-script", "post-autoload-dump"]:
        run(compose_args(row) + ["run", "--rm", "--no-deps", "--user", "33:33", service, "php", "artisan", "optimize"])
    if row["kind"] == "laravel" and args and args[0] in ("update", "require", "remove"):
        print("Nota: post-update-cmd no se ejecuta (el código es de solo lectura para 33:33). "
              "Publica los assets de paquetes durante la construcción o el despliegue.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="action", required=True)
    c = commands.add_parser("composer", help="composer con el procedimiento seguro para Laravel")
    c.add_argument("domain")
    c.add_argument("--service", default="web")
    c.add_argument("--verbose", action="store_true")
    c.add_argument("args", nargs=argparse.REMAINDER)
    b = commands.add_parser("build", help="construir imágenes mostrando la salida solo si falla")
    b.add_argument("domain")
    b.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    os.umask(0o022)
    row = site(args.domain)
    if args.action == "build":
        build_quiet(row, args.verbose)
    elif args.action == "composer":
        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        composer(row, extra, args.service, args.verbose)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: el comando terminó con código {exc.returncode}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, StopIteration) as exc:
        print(f"ERROR: {exc or 'sitio no registrado'}", file=sys.stderr)
        sys.exit(1)
