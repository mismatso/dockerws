#!/usr/bin/env python3
"""Inventario local: cada sitio contiene sus metadatos; sin registro duplicado."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def settings():
    result = json.loads((ROOT / "config/defaults.json").read_text())
    local = ROOT / "config/local.json"
    if local.exists():
        result.update(json.loads(local.read_text()))
    return result


def domain_valid(name):
    return (isinstance(name, str) and len(name) <= 253 and "." in name
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                    for p in name.split(".")) and not name.split(".")[-1].isdigit())


def validate(row):
    if row.get("schema") != 1:
        raise ValueError("Versión de site.json desconocida")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,29}", row["id"]):
        raise ValueError("ID: 1–30 caracteres minúsculos, números y guiones; iniciar con letra")
    if not all(domain_valid(d) for d in [row["domain"], *row["aliases"]]):
        raise ValueError("Dominio inválido; usar ASCII/punycode, sin URL ni wildcard")
    if row["kind"] not in ("wordpress", "laravel", "php", "static"):
        raise ValueError("Tipo de sitio inválido")
    if row["php"] not in settings()["php_versions"]:
        raise ValueError("Versión PHP no admitida en config/defaults.json")
    db = row["database"]
    if db["engine"] not in ("none", "mysql", "mariadb", "postgres", "sqlite"):
        raise ValueError("Motor inválido")
    modes = ("none",) if db["engine"] == "none" else (("file",) if db["engine"] == "sqlite" else ("container", "host"))
    if db["mode"] not in modes:
        raise ValueError("Modalidad incompatible con el motor")
    if db["mode"] == "container" and db["port"] != (5432 if db["engine"] == "postgres" else 3306):
        raise ValueError("El contenedor dedicado utiliza el puerto estándar del motor")
    if row["kind"] == "wordpress" and db["engine"] not in ("mysql", "mariadb"):
        raise ValueError("WordPress requiere MySQL o MariaDB")
    if row["kind"] == "static" and db["engine"] != "none":
        raise ValueError("Un sitio estático no utiliza base de datos")
    for key in ("name", "user"):
        if not re.fullmatch(r"[a-z][a-z0-9]{0,31}", db[key]):
            raise ValueError("Identificador SQL inválido")
    if not isinstance(db["port"], int) or not 1 <= db["port"] <= 65535:
        raise ValueError("Puerto inválido")
    if not re.fullmatch(r"/[a-zA-Z0-9_/.-]+", db["socket_dir"]) or ".." in Path(db["socket_dir"]).parts:
        raise ValueError("Directorio del socket inválido")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", db["socket_name"]) or db["socket_name"] in (".", ".."):
        raise ValueError("Nombre del socket inválido")


def sites():
    rows, ids, domains, db_names = [], set(), set(), set()
    for path in sorted((ROOT / "sites").glob("*/site.json")):
        if path.parent.name.startswith("."):
            continue  # El generador publica una carpeta .new-* mediante rename atómico.
        row = json.loads(path.read_text())
        validate(row)
        if path.parent.name != row["domain"] or path.parent.is_symlink():
            raise ValueError("Ruta de sitio incoherente o enlace simbólico")
        if row["id"] in ids or row["database"]["name"] in db_names:
            raise ValueError("ID o nombre de base de datos duplicado")
        ids.add(row["id"])
        db_names.add(row["database"]["name"])
        for name in [row["domain"], *row["aliases"]]:
            if name in domains:
                raise ValueError("Dominio o alias duplicado: " + name)
            domains.add(name)
        rows.append(row)
    return rows


def site(domain):
    return next(r for r in sites() if r["domain"] == domain)


def compose_args(row):
    path = ROOT / "sites" / row["domain"]
    return ["docker", "compose", "--project-directory", str(path), "--env-file", str(path / ".env"),
            "-f", str(path / "compose.yaml")]


if __name__ == "__main__":
    try:
        if sys.argv[1] == "list":
            for row in sites():
                if len(sys.argv) < 3 or sys.argv[2] != "database" or row["database"]["engine"] != "none":
                    print(row["domain"])
        elif sys.argv[1] == "field":
            value = site(sys.argv[2])
            for key in sys.argv[3].split("."):
                value = value[key]
            print(" ".join(value) if isinstance(value, list) else value)
        else:
            raise ValueError("Comando inválido")
    except (ValueError, StopIteration, KeyError, IndexError) as exc:
        sys.exit(f"Inventario inválido: {exc}")
