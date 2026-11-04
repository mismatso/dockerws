#!/usr/bin/env python3
"""Respaldos atómicos, privados y verificables de bases de datos por sitio."""
import argparse
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from registry import ROOT, settings, sites, site, compose_args
from database import dump_command, restore_command, query


def private_directory(path):
    path = Path(path)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError(f"No se admiten enlaces simbólicos: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError(f"Directorio debe ser propio y tener permisos 0700: {path}")
    return path


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sqlite_snapshot(source, target):
    if not source.is_file() or source.is_symlink():
        raise ValueError("Archivo SQLite ausente o symlink")
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst, pages=256)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite quick_check falló")


def backup_one(row, temporary):
    engine = row["database"]["engine"]
    if row["database"]["mode"] == "host" and not (ROOT / "sites" / row["domain"] / "host-db.ready").exists():
        raise ValueError("Base compartida pendiente de aprovisionar")
    filename = "database.sqlite" if engine == "sqlite" else "database.sql.gz"
    destination = temporary / filename
    if engine == "sqlite":
        sqlite_snapshot(ROOT / "sites" / row["domain"] / "data/sqlite/database.sqlite", destination)
    else:
        command = dump_command(row)
        # stderr a archivo privado: no llenar buffers ni mezclar errores con SQL.
        with tempfile.TemporaryFile() as errors, destination.open("xb") as output:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
            try:
                with gzip.GzipFile(filename="", mode="wb", fileobj=output) as compressed:
                    shutil.copyfileobj(process.stdout, compressed)
                process.stdout.close()
                if process.wait() != 0:
                    raise RuntimeError("El cliente de respaldo terminó con error; comprobar servicio y acceso administrativo")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        with gzip.open(destination, "rb") as stream:
            if not stream.read(1):
                raise ValueError("Dump vacío")
            while stream.read(1024 * 1024):
                pass
    destination.chmod(0o600)
    manifest = {"schema": 1, "domain": row["domain"], "engine": engine,
                "database": row["database"]["name"], "file": filename,
                "sha256": checksum(destination), "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (temporary / "manifest.json").chmod(0o600)


def verify(row, folder):
    if folder.is_symlink() or (folder / "manifest.json").is_symlink():
        raise ValueError("Respaldo enlazado no admitido")
    manifest = json.loads((folder / "manifest.json").read_text())
    expected = "database.sqlite" if row["database"]["engine"] == "sqlite" else "database.sql.gz"
    if (manifest.get("schema") != 1 or manifest.get("domain") != row["domain"]
            or manifest.get("engine") != row["database"]["engine"] or manifest.get("database") != row["database"]["name"]
            or manifest.get("file") != expected):
        raise ValueError("El respaldo no corresponde a este sitio/motor/base")
    source = folder / expected
    if not source.is_file() or source.is_symlink() or checksum(source) != manifest["sha256"]:
        raise ValueError("Checksum SHA-256 incorrecto")
    return source


def prune(row, folder, days):
    cutoff = time.time() - days * 86400
    for path in folder.iterdir():
        if (re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8}", path.name) and not path.is_symlink()
                and path.is_dir() and path.stat().st_mtime < cutoff):
            try:
                verify(row, path)
                # No borrar directorios con archivos agregados por el administrador.
                if {p.name for p in path.iterdir()} == {"manifest.json", "database.sqlite" if row["database"]["engine"] == "sqlite" else "database.sql.gz"}:
                    shutil.rmtree(path)
            except (ValueError, OSError):
                pass


def backup_all(args, base):
    cfg = settings()
    days = int(os.environ.get("RETENTION_DAYS", cfg["retention_days"]))
    timeout = int(os.environ.get("BACKUP_TIMEOUT", cfg["backup_timeout"]))
    if days < 1 or timeout < 1:
        raise ValueError("Retención y timeout deben ser positivos")
    rows = [site(args.domain)] if args.domain else sites()
    failed = False
    for row in rows:
        if row["database"]["engine"] == "none":
            continue
        pending = None
        try:
            folder = private_directory(base / row["domain"])
            pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=folder))
            command = [sys.executable, str(Path(__file__).resolve()), "_one", row["domain"], str(pending)]
            process = subprocess.Popen(command, start_new_session=True)
            try:
                status = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise RuntimeError("Se agotó el tiempo de respaldo")
            if status:
                raise RuntimeError("Falló el respaldo; se conserva la copia anterior")
            verify(row, pending)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            destination = folder / stamp
            pending.rename(destination)
            pending = None
            print(f"OK {row['domain']}: {destination}", flush=True)
            prune(row, folder, days)
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"ERROR {row['domain']}: {exc}", file=sys.stderr, flush=True)
            failed = True
        finally:
            if pending is not None:
                shutil.rmtree(pending)
    return int(failed)


def ensure_writers_stopped(row):
    running = subprocess.run(compose_args(row) + ["ps", "--status", "running", "--services"],
                             capture_output=True, text=True, check=True).stdout.split()
    if any(service != "db" for service in running):
        raise ValueError("Detener web, scheduler y workers antes de restaurar o importar")


def open_sql(source):
    """Abre .sql o .sql.gz según su contenido, no según la extensión."""
    with Path(source).open("rb") as stream:
        magic = stream.read(2)
    return gzip.open(source, "rb") if magic == b"\x1f\x8b" else Path(source).open("rb")


# Un dump importado no debe cambiar de base ni crear otras: se ejecuta como administrador del motor.
FORBIDDEN = {
    "mysql": re.compile(rb"\s*(USE\s|CREATE\s+(DATABASE|SCHEMA)\b|DROP\s+(DATABASE|SCHEMA)\b|CREATE\s+USER\b|GRANT\s)", re.I),
    "postgres": re.compile(rb"\s*(\\c(onnect)?\s|CREATE\s+DATABASE\b|DROP\s+DATABASE\b|CREATE\s+ROLE\b|ALTER\s+ROLE\b)", re.I),
}


def scan_sql(row, source):
    """Lee el archivo completo (valida el CRC de gzip) y rechaza sentencias fuera de la base del sitio."""
    pattern = FORBIDDEN["postgres" if row["database"]["engine"] == "postgres" else "mysql"]
    empty = True
    try:
        with open_sql(source) as stream:
            for number, line in enumerate(stream, 1):
                empty = empty and not line.strip()
                if pattern.match(line[:80]):
                    text = line[:60].decode(errors="replace").strip()
                    raise ValueError(f"Línea {number}: «{text}». El dump debe contener solo la base del sitio "
                                     "(sin USE, CREATE/DROP DATABASE, usuarios ni roles). Genera el dump sin --databases.")
    except (EOFError, gzip.BadGzipFile) as exc:
        raise ValueError(f"Archivo comprimido dañado o incompleto: {exc}") from exc
    if empty:
        raise ValueError("El archivo SQL está vacío")


def load_sql(row, source):
    db = row["database"]
    if db["engine"] == "postgres":
        count = query(row, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                           "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%';", db["name"])
        if count != "0":
            raise ValueError("PostgreSQL requiere una base vacía para restaurar o importar; ver docs/BACKUPS.md")
    with open_sql(source) as stream, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(restore_command(row), stdin=subprocess.PIPE, stderr=errors, stdout=subprocess.DEVNULL)
        try:
            if db["engine"] == "postgres":
                process.stdin.write(f"SET ROLE {db['user']};\n".encode())
            shutil.copyfileobj(stream, process.stdin)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError("Carga SQL fallida; mantener aplicación detenida y revisar compatibilidad")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def restore(args):
    row = site(args.domain)
    if not args.confirm_overwrite:
        raise ValueError("Se requiere --confirm-overwrite; detener antes web, workers y cualquier escritor externo")
    source = verify(row, Path(args.folder))
    ensure_writers_stopped(row)
    db = row["database"]
    if db["engine"] == "sqlite":
        target = ROOT / "sites" / row["domain"] / "data/sqlite/database.sqlite"
        if target.is_symlink():
            raise ValueError("No restaurar sobre un symlink")
        # Backup API escribe en el archivo existente y maneja WAL; no usar cp.
        sqlite_snapshot(source, target)
        os.chown(target, 33, 33)
        target.chmod(0o640)
    else:
        with gzip.open(source, "rb") as stream:
            while stream.read(1024 * 1024):
                pass  # Validar CRC antes de iniciar cualquier escritura.
        load_sql(row, source)
    print(f"Restauración finalizada: {args.domain}")


def import_sql(args):
    """Importa un dump externo (.sql o .sql.gz) igual en las modalidades host y container."""
    row = site(args.domain)
    db = row["database"]
    if db["engine"] in ("none", "sqlite"):
        raise ValueError("db-import admite MySQL, MariaDB y PostgreSQL; para SQLite usa restore.sh o sqlite3")
    if not args.confirm_overwrite:
        raise ValueError("Se requiere --confirm-overwrite: el dump reemplaza tablas de la base del sitio")
    source = Path(args.file)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Se espera un archivo regular, no un enlace simbólico")
    if db["mode"] == "host" and not (ROOT / "sites" / row["domain"] / "host-db.ready").exists():
        raise ValueError("Base compartida pendiente de aprovisionar")
    scan_sql(row, source)
    ensure_writers_stopped(row)
    load_sql(row, source)
    print(f"Importación finalizada: {args.domain} ({db['engine']}/{db['mode']})")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="action", required=True)
    b = commands.add_parser("backup")
    b.add_argument("domain", nargs="?")
    r = commands.add_parser("restore")
    r.add_argument("domain")
    r.add_argument("folder")
    r.add_argument("--confirm-overwrite", action="store_true")
    i = commands.add_parser("import")
    i.add_argument("domain")
    i.add_argument("file")
    i.add_argument("--confirm-overwrite", action="store_true")
    one = commands.add_parser("_one")
    one.add_argument("domain")
    one.add_argument("folder")
    args = p.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    if args.action == "_one":
        backup_one(site(args.domain), Path(args.folder))
        return 0
    base = private_directory(Path(os.environ.get("BACKUP_DIR", settings()["backup_dir"])))
    lock_path = base / ".backup.lock"
    if lock_path.is_symlink():
        raise ValueError("Lock enlazado no admitido")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Ya hay un respaldo o restauración en curso")
        if args.action == "backup":
            return backup_all(args, base)
        return restore(args) if args.action == "restore" else import_sql(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, StopIteration, subprocess.SubprocessError) as exc:
        # CalledProcessError puede contener SQL; no mostrar comando ni credenciales.
        print("ERROR: " + ("Falló un cliente de base de datos" if isinstance(exc, subprocess.SubprocessError) else str(exc)), file=sys.stderr)
        sys.exit(1)
