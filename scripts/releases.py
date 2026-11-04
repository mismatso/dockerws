#!/usr/bin/env python3
"""Releases atómicos: releases/FECHA, app como enlace al release activo y storage compartido.

Este módulo solo opera sobre el sistema de archivos; deploy.py orquesta Docker.
Docker resuelve el enlace app al crear cada contenedor: los contenedores en marcha conservan
su release hasta que se recrean, y cambiar el enlace es un rename(2) atómico.
"""
import datetime as dt
import json
import os
import re
import shutil
from pathlib import Path
from registry import ROOT

NAME = re.compile(r"\d{8}T\d{6}Z(-\d{1,3})?")
META = ".dockerws-release.json"
OVERLAY = "90-releases.yaml"  # Se carga al final para ganar sobre los montajes de storage anteriores.
APP_STORAGE, SHARED_STORAGE = "./app/storage", "./shared/storage"
STORAGE_TARGET = "/var/www/html/storage"


def site_dir(row):
    return ROOT / "sites" / row["domain"]


def releases_dir(row):
    return site_dir(row) / "releases"


def enabled(row):
    app = site_dir(row) / "app"
    return app.is_symlink() and os.readlink(app).startswith("releases/")


def check_name(name):
    if not NAME.fullmatch(name):
        raise ValueError(f"Nombre de release inválido: {name}")
    return name


def path(row, name):
    folder = releases_dir(row) / check_name(name)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f"No existe el release {name}")
    return folder


def current(row):
    if not enabled(row):
        raise ValueError("El sitio no usa releases; inicialízalo con deploy.sh DOMINIO --init-releases")
    return check_name(Path(os.readlink(site_dir(row) / "app")).name)


def names(row):
    folder = releases_dir(row)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if NAME.fullmatch(p.name) and p.is_dir() and not p.is_symlink())


def new_name(row, now=None):
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    folder = releases_dir(row)
    existing = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
    name, counter = stamp, 1
    while name in existing:
        counter += 1
        name = f"{stamp}-{counter}"
    return name


def switch(row, name):
    """Apunta app al release con un rename atómico: nunca existe un instante sin app."""
    path(row, name)
    site = site_dir(row)
    temp = site / ".app.tmp"
    if temp.is_symlink() or temp.exists():
        temp.unlink()
    os.symlink(f"releases/{name}", temp)
    app = site / "app"
    if app.exists() and not app.is_symlink():
        temp.unlink()
        raise ValueError("app/ es un directorio; el sitio no usa releases")
    os.replace(temp, app)


def read_meta(row, name):
    file = path(row, name) / META
    try:
        return json.loads(file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_meta(folder, data):
    file = Path(folder) / META
    temp = file.with_name(file.name + ".tmp")
    temp.write_text(json.dumps({"schema": 1, **data}, indent=2) + "\n")
    temp.chmod(0o644)
    os.replace(temp, file)


def previous(row):
    """Release anterior al activo: el registrado al desplegar o, si no existe, el más reciente más viejo."""
    active = current(row)
    recorded = read_meta(row, active).get("previous")
    available = names(row)
    if recorded in available and recorded != active:
        return recorded
    older = [n for n in available if n < active]
    if not older:
        raise ValueError("No hay un release anterior al activo")
    return older[-1]


def migration_files(folder):
    """Migraciones que conoce un release: database/migrations y las que traen sus paquetes."""
    folder = Path(folder)
    files = list(folder.glob("database/migrations/*.php")) + list(folder.glob("vendor/*/*/database/migrations/*.php"))
    return sorted({f.stem for f in files})


def prune(row, keep):
    """Borra releases viejos: conserva los `keep` más recientes, el activo y su anterior."""
    if keep < 2:
        raise ValueError("keep_releases debe ser al menos 2")
    available = names(row)
    protected = set(available[-keep:])
    if enabled(row):
        active = current(row)
        protected.add(active)
        try:
            protected.add(previous(row))
        except ValueError:
            pass
    removed = []
    for name in available:
        if name not in protected:
            shutil.rmtree(releases_dir(row) / name)
            removed.append(name)
    return removed


def clean_pending(row):
    folder = releases_dir(row)
    if folder.is_dir():
        for item in folder.glob(".pending-*"):
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)


# --- Overlay de storage compartido ----------------------------------------------------------

def storage_sources(config, row):
    """Servicios del Compose resuelto que montan app/storage (rutas absolutas de compose config)."""
    source = str(site_dir(row) / "app/storage")
    found = []
    for name, service in config.get("services", {}).items():
        for volume in service.get("volumes", []) or []:
            if isinstance(volume, dict) and volume.get("source") in (source, APP_STORAGE):
                found.append((name, volume["target"]))
    return found


def worker_overlays(row):
    """Overlays de workers generados por DockerWS: se editan en su lugar en vez de duplicar servicios."""
    folder = site_dir(row) / "compose.d"
    return sorted(folder.glob("50-*.yaml")) if folder.is_dir() else []


def rewrite_worker_storage(row, old, new):
    from workers import write_overlay
    changed = []
    for file in worker_overlays(row):
        data = json.loads(file.read_text())
        touched = False
        for service in data.get("services", {}).values():
            for volume in service.get("volumes", []) or []:
                if isinstance(volume, dict) and volume.get("source") == old:
                    volume["source"] = new
                    touched = True
        if touched:
            write_overlay(file, json.dumps(data, indent=2) + "\n")
            changed.append(file)
    return changed


def storage_overlay(config, row):
    """Overlay que monta shared/storage en lugar de app/storage en los servicios de compose.yaml."""
    workers = set()
    for file in worker_overlays(row):
        workers |= set(json.loads(file.read_text()).get("services", {}))
    services = {}
    for name, target in storage_sources(config, row):
        if name in workers:
            continue
        services.setdefault(name, {"volumes": []})["volumes"].append(
            {"type": "bind", "source": SHARED_STORAGE, "target": target, "read_only": False,
             "bind": {"create_host_path": False}})
    return {"services": services} if services else None
