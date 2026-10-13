#!/usr/bin/env python3
"""Crea base y cuenta locales. Nunca adopta ni borra recursos SQL existentes."""
import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from registry import ROOT, site
from scaffold import write, db_sql
from database import private_file, query


def postgres_hba(row):
    db = row["database"]
    path = Path(query(row, "SHOW hba_file;"))
    info = path.stat()
    original = path.read_text()
    marker = "# dockerws " + row["domain"]
    if marker in original:
        raise ValueError("Ya existe una regla HBA para este sitio; revisar antes de continuar")
    rules = (f"{marker}\nlocal {db['name']} {db['user']} scram-sha-256\n"
             f"local all {db['user']} reject\nhost all {db['user']} 0.0.0.0/0 reject\n"
             f"host all {db['user']} ::/0 reject\n")
    backup = ROOT / "sites" / row["domain"] / "secrets/pg_hba.before"
    write(backup, original)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".dockerws-hba-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(rules + original)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
        os.replace(temp, path)
        if query(row, "SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;") != "0":
            raise ValueError("PostgreSQL rechazó las reglas HBA")
        if query(row, "SELECT pg_reload_conf();") != "t":
            raise ValueError("PostgreSQL no pudo recargar la configuración")
    except BaseException:
        path.write_text(original)
        query(row, "SELECT pg_reload_conf();")
        raise
    finally:
        Path(temp).unlink(missing_ok=True)


def provision(args):
    row = site(args.domain)
    db = row["database"]
    if db["mode"] != "host":
        raise ValueError("El sitio no usa una instancia del host")
    path = ROOT / "sites" / row["domain"]
    if (path / "host-db.ready").exists():
        print("La base ya fue aprovisionada; credenciales intactas.")
        return
    if args.admin_cnf:
        if db["engine"] == "postgres":
            raise ValueError("PostgreSQL utiliza el administrador local postgres mediante peer")
        value = str(private_file(Path(args.admin_cnf).resolve()))
        config = path / "host-admin.json"
        if config.exists():
            if json.loads(config.read_text())["admin_cnf"] != value:
                raise ValueError("El archivo administrativo ya está configurado")
        else:
            write(config, json.dumps({"admin_cnf": value}) + "\n")
    # Validar socket antes de tocar cuentas; los contenedores montarán su directorio.
    socket = Path(db["socket_dir"]) / (f".s.PGSQL.{db['port']}" if db["engine"] == "postgres" else db["socket_name"])
    if not socket.is_socket():
        raise ValueError(f"No existe el socket {socket}; instalar/iniciar el motor en Debian primero")
    if db["engine"] == "postgres":
        existing = query(row, f"SELECT (SELECT count(*) FROM pg_database WHERE datname='{db['name']}') + "
                             f"(SELECT count(*) FROM pg_roles WHERE rolname='{db['user']}');")
    else:
        existing = query(row, f"SELECT (SELECT count(*) FROM information_schema.schemata WHERE schema_name='{db['name']}') + "
                             f"(SELECT count(*) FROM mysql.user WHERE user='{db['user']}');")
    if existing != "0":
        raise ValueError("Ya existe la base o el usuario; no se adopta ni sobrescribe. Si hubo una interrupción, revisar docs/DATABASES.md")
    password = (path / "secrets/db-password").read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise ValueError("Contraseña generada inválida")
    # Si falla después de CREATE, se conservan recursos para diagnóstico, nunca se borran automáticamente.
    query(row, db_sql(row, password, "localhost"))
    if db["engine"] == "postgres":
        query(row, "REVOKE CREATE ON SCHEMA public FROM PUBLIC;", database=db["name"])
        postgres_hba(row)
    write(path / "host-db.ready", "Base y usuario creados por DockerWS.\n")
    print(f"Base {db['name']} y usuario {db['user']} creados para {args.domain}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("--admin-cnf")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Ejecutar como root")
    os.umask(0o077)
    try:
        with open("/run/lock/dockerws-host-db.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            provision(args)
    except subprocess.CalledProcessError:
        # El stderr de un cliente puede repetir el SQL y su contraseña.
        raise SystemExit("Falló una operación SQL administrativa. Revisar acceso por socket y posibles recursos parciales; no se modifican credenciales existentes.")
    except (ValueError, OSError, StopIteration) as exc:
        raise SystemExit(str(exc))
