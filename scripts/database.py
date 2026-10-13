#!/usr/bin/env python3
"""Comandos administrativos por motor. Ninguna contraseña se pasa por argv."""
import os
import json
import stat
import subprocess
from pathlib import Path
from registry import ROOT, compose_args


def private_file(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError(f"Se requiere archivo privado (0600/0400), propio y sin symlink: {path}")
    return path


def admin_cnf(row):
    path = ROOT / "sites" / row["domain"] / "host-admin.json"
    if path.exists():
        return str(private_file(json.loads(private_file(path).read_text())["admin_cnf"]))
    return None


def mysql_command(row, dump=False):
    db = row["database"]
    binary = ("mariadb-dump" if dump else "mariadb") if db["engine"] == "mariadb" else ("mysqldump" if dump else "mysql")
    if db["mode"] == "container":
        return compose_args(row) + ["exec", "-T", "--user", "0", "db", binary,
                                    "--defaults-extra-file=/run/secrets/db-client.cnf"]
    cnf = admin_cnf(row)
    return ([binary] + (["--defaults-extra-file=" + cnf] if cnf else ["--user=root"])
            + ["--protocol=socket", "--socket=" + str(Path(db["socket_dir"]) / db["socket_name"])])


def postgres_command(row, binary="psql", database="postgres"):
    db = row["database"]
    if db["mode"] == "container":
        command = compose_args(row) + ["exec", "-T", "--user", "postgres", "db", binary, "-U", "postgres"]
    else:
        command = ["runuser", "-u", "postgres", "--", binary, "-h", db["socket_dir"], "-p", str(db["port"])]
    command += ["-d", database]
    if binary == "psql":
        command += ["-X", "-v", "ON_ERROR_STOP=1", "-A", "-t"]
    return command


def query(row, sql, database=None):
    command = postgres_command(row, database=database or "postgres") if row["database"]["engine"] == "postgres" else mysql_command(row) + ["-N", "-B"] + (["--database=" + database] if database else [])
    return subprocess.run(command, input=sql, text=True, capture_output=True, check=True, timeout=60).stdout.strip()


def dump_command(row):
    db = row["database"]
    if db["engine"] == "postgres":
        return postgres_command(row, "pg_dump", db["name"]) + ["--no-owner", "--no-privileges", "--format=plain"]
    count = query(row, f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{db['name']}' "
                      "AND table_type='BASE TABLE' AND engine <> 'InnoDB';")
    if not count.isdigit():
        raise ValueError("No fue posible comprobar los motores de las tablas")
    consistency = "--lock-all-tables" if int(count) else "--single-transaction"
    if int(count):
        print(f"{row['domain']}: tablas no transaccionales; bloqueo de tablas durante el dump", flush=True)
    command = mysql_command(row, dump=True) + [consistency, "--quick", "--routines", "--events", "--triggers",
               "--hex-blob", "--default-character-set=utf8mb4", "--no-tablespaces"]
    if db["engine"] == "mysql":
        command.append("--set-gtid-purged=OFF")
    return command + [db["name"]]


def restore_command(row):
    db = row["database"]
    if db["engine"] == "postgres":
        return postgres_command(row, database=db["name"]) + ["--single-transaction"]
    return mysql_command(row) + ["--database=" + db["name"], "--binary-mode"]
