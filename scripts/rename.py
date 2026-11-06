#!/usr/bin/env python3
"""Renombra un sitio creado con versiones anteriores a los nombres derivados del dominio.

Proyecto de Compose dockerws-sHASH → dws-DOMINIO-HASH (contenedores, imágenes) y, en MySQL/MariaDB,
base y usuario dwsHASH → dws_DOMINIO_HASH. El ID de site.json, las redes del proxy y el alias web-ID
no cambian: no se ven en docker ps y el proxy sigue igual.

Sin --apply solo muestra el plan. Con --apply, cualquier error vuelve al estado anterior. Lo viejo
(base, usuario, imágenes, redes) se conserva hasta --drop-old.
"""
import argparse
import datetime as dt
import fcntl
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from registry import ROOT, compose_args, compose_files, site
import backups
import database
import deploy
import scaffold

LEGACY_PROJECT = re.compile(r"dockerws-s[0-9a-f]{12}")
LEGACY_DATABASE = re.compile(r"dws[0-9a-f]{12}")
RECORD = ".rename.json"
SNAPSHOT = ".rename-antes.tgz"
ENV_FILES = ("runtime.env", "app.env")
ENV_KEYS = ("DB_DATABASE", "DB_USERNAME")
CRON_PATHS = (Path("/etc/crontab"), Path("/etc/cron.d"), Path("/var/spool/cron/crontabs"))


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def output(command):
    return run(command, capture_output=True, text=True).stdout


def site_path(row):
    return ROOT / "sites" / row["domain"]


def project_name(row):
    return json.loads(output(compose_args(row) + ["config", "--format", "json", "--no-interpolate"]))["name"]


def project_containers(project):
    return output(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).split()


def project_volumes(project):
    """{clave en Compose: nombre en Docker} de los volúmenes del proyecto."""
    lines = output(["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}",
                    "--format", '{{.Name}} {{.Label "com.docker.compose.volume"}}']).splitlines()
    return {key: name for name, key in (line.split() for line in lines if line.strip())}


def running(row):
    return output(compose_args(row) + ["ps", "--status", "running", "--services"]).split()


def env_lines(row, old):
    """Líneas DB_DATABASE/DB_USERNAME con el nombre viejo y cualquier otra mención (que impide continuar)."""
    found, others = [], []
    for name in ENV_FILES:
        path = site_path(row) / name
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key in ENV_KEYS and value.strip("'\"") == old:
                found.append((name, key))
            elif old in line:
                others.append(f"{name}: {key}")
    return found, others


def rewrite_env(row, old, new):
    pattern = re.compile(rf"^({'|'.join(ENV_KEYS)})=([\"']?){re.escape(old)}\2$", re.M)
    for name in ENV_FILES:
        path = site_path(row) / name
        if path.is_file():
            text = path.read_text()
            replaced = pattern.sub(lambda m: f"{m[1]}={m[2]}{new}{m[2]}", text)
            if replaced != text:
                write_keep_mode(path, replaced)


def write_keep_mode(path, text):
    info = path.stat()
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text)
    if os.geteuid() == 0:
        os.chown(temp, info.st_uid, info.st_gid)
    temp.chmod(info.st_mode & 0o7777)
    os.replace(temp, path)


def rewrite_compose(row, old, new, volumes):
    """Cambia el nombre del proyecto (y de las imágenes que lo usan) en compose.yaml y compose.d/.

    Los volúmenes del proyecto viejo pasan a declararse external con su nombre actual: los datos no se mueven."""
    for path in compose_files(row):
        text = path.read_text()
        if old in text:
            write_keep_mode(path, text.replace(old, new))
    if volumes:
        path = site_path(row) / "compose.yaml"
        data = json.loads(path.read_text())
        for key, name in volumes.items():
            data.setdefault("volumes", {})[key] = {"external": True, "name": name}
        write_keep_mode(path, json.dumps(data, indent=2) + "\n")


def cron_mentions(*names):
    found = []
    for base in CRON_PATHS:
        files = sorted(base.iterdir()) if base.is_dir() else [base] if base.is_file() else []
        for path in files:
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if any(name in text for name in names):
                found.append(str(path))
    return found


def mysql_count(row, sql):
    value = database.query(row, sql)
    if not value.isdigit():
        raise ValueError(f"Respuesta inesperada de la base: {value!r}")
    return int(value)


class Plan:
    def __init__(self, row, containers_only=False):
        self.row = row
        db = row["database"]
        ident, db_name = scaffold.derive_names(row["domain"])
        self.old_project = project_name(row)
        self.new_project = scaffold.PROJECT_PREFIX + ident
        self.rename_project = bool(LEGACY_PROJECT.fullmatch(self.old_project))
        if self.rename_project and self.old_project not in (site_path(row) / "compose.yaml").read_text():
            raise ValueError(f"compose.yaml no declara name: {self.old_project}; edítalo a mano")
        self.old_db, self.new_db = db["name"], db_name
        self.rename_db = (not containers_only and db["engine"] not in ("none", "sqlite")
                          and bool(LEGACY_DATABASE.fullmatch(db["name"])) and db["user"] == db["name"])
        if self.rename_db and db["engine"] == "postgres":
            raise ValueError("Renombrar la base no está soportado en PostgreSQL; usa --containers-only")
        self.volumes = project_volumes(self.old_project) if self.rename_project else {}
        if self.volumes:
            try:
                json.loads((site_path(row) / "compose.yaml").read_text())
            except json.JSONDecodeError:
                raise ValueError("El proyecto tiene volúmenes y compose.yaml no es JSON; no se puede declararlos "
                                 "external automáticamente") from None
        self.env, others = env_lines(row, self.old_db) if self.rename_db else ([], [])
        if self.rename_db:
            if others:
                raise ValueError("El nombre de la base aparece en otras variables: " + ", ".join(others)
                                 + ". Cámbialas a mano o usa --containers-only")
            if {key for _, key in self.env} != set(ENV_KEYS):
                raise ValueError(f"No se encontraron DB_DATABASE y DB_USERNAME={self.old_db} en runtime.env ni app.env")
            self.host = "localhost" if db["mode"] == "host" else "%"
        self.cron = cron_mentions(self.old_project, self.old_db) if self.rename_project or self.rename_db else []

    @property
    def empty(self):
        return not self.rename_project and not self.rename_db

    def new_row(self):
        row = json.loads(json.dumps(self.row))
        if self.rename_db:
            row["database"].update(name=self.new_db, user=self.new_db)
        return row

    def describe(self):
        lines = [f"Sitio: {self.row['domain']}"]
        if self.rename_project:
            lines.append(f"  Contenedores: {self.old_project}-* → {self.new_project}-*")
            for key, name in self.volumes.items():
                lines.append(f"  Volumen {key}: se conserva {name} (external)")
        if self.rename_db:
            lines.append(f"  Base y usuario: {self.old_db} → {self.new_db} (respaldo, copia e importación)")
            lines.append("  Variables: " + ", ".join(f"{key} en {name}" for name, key in self.env))
        if self.empty:
            lines.append("  Ya usa los nombres nuevos (o personalizados): nada que hacer.")
        for path in self.cron:
            lines.append(f"  AVISO: {path} menciona el nombre viejo; actualízalo después.")
        return "\n".join(lines)


class Rename:
    def __init__(self, plan):
        self.plan = plan
        self.row = plan.row
        self.path = site_path(self.row)
        self.snapshot = None
        self.created_db = False
        self.backup = ""
        self.was_running = []

    def step(self, text):
        print(f"==> {text}", flush=True)

    def take_snapshot(self):
        """Copia de los archivos que se modifican: en memoria para volver atrás y en un .tgz para revisarlos."""
        names = ["site.json", *ENV_FILES, "secrets/init.sql", *(str(p.relative_to(self.path)) for p in compose_files(self.row))]
        self.files = {}
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for name in dict.fromkeys(names):
                path = self.path / name
                if path.is_file() and not path.is_symlink():
                    info = path.stat()
                    self.files[name] = (path.read_bytes(), info.st_mode & 0o7777, info.st_uid, info.st_gid)
                    tar.add(path, arcname=name)
        self.snapshot = buffer.getvalue()
        target = self.path / SNAPSHOT
        target.write_bytes(self.snapshot)
        target.chmod(0o600)

    def restore_snapshot(self):
        for name, (data, mode, uid, gid) in self.files.items():
            path = self.path / name
            temp = path.with_name(f".{path.name}.tmp")
            temp.write_bytes(data)
            if os.geteuid() == 0:
                os.chown(temp, uid, gid)
            temp.chmod(mode)
            os.replace(temp, path)

    def check_database(self):
        plan, row = self.plan, self.row
        if not plan.rename_db:
            return
        password = (self.path / "secrets/db-password").read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9]+", password):
            raise ValueError("secrets/db-password tiene un formato inesperado")
        self.password = password
        objects = mysql_count(row, (
            f"SELECT (SELECT COUNT(*) FROM information_schema.routines WHERE routine_schema='{plan.old_db}')"
            f" + (SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema='{plan.old_db}')"
            f" + (SELECT COUNT(*) FROM information_schema.views WHERE table_schema='{plan.old_db}')"
            f" + (SELECT COUNT(*) FROM information_schema.events WHERE event_schema='{plan.old_db}');"))
        if objects:
            raise ValueError("La base tiene rutinas, triggers, vistas o eventos (con DEFINER del usuario viejo); "
                             "renómbrala a mano o usa --containers-only")
        if mysql_count(row, f"SELECT (SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='{plan.new_db}')"
                            f" + (SELECT COUNT(*) FROM mysql.user WHERE user='{plan.new_db}');"):
            raise ValueError(f"Ya existe la base o el usuario {plan.new_db}")

    def tables(self, row, name):
        return mysql_count(row, f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{name}';")

    def execute(self):
        plan, row = self.plan, self.row
        self.was_running = running(row)
        self.check_database()
        self.take_snapshot()
        try:
            if plan.rename_project:
                self.step("Cambiando el nombre del proyecto en compose.yaml y compose.d/")
                rewrite_compose(row, plan.old_project, plan.new_project, plan.volumes)
                run(compose_args(row) + ["config", "--quiet"])
                self.step("Construyendo las imágenes con el nombre nuevo (el sitio sigue en línea)")
                deploy.build_quiet(row)
                self.step("Deteniendo los contenedores viejos")
                containers = project_containers(plan.old_project)
                if containers:
                    run(["docker", "rm", "-f", *containers], stdout=subprocess.DEVNULL)
            else:
                self.step("Deteniendo el sitio")
                run(compose_args(row) + ["stop"])
            if plan.rename_db:
                self.rename_database()
            if self.was_running:
                self.start(plan.new_row())
        except BaseException:
            self.rollback()
            raise
        record = {"schema": 1, "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                  "old_project": plan.old_project if plan.rename_project else None,
                  "old_database": plan.old_db if plan.rename_db else None,
                  "host": getattr(plan, "host", None), "backup": self.backup,
                  "volumes": sorted(plan.volumes.values())}
        (self.path / RECORD).write_text(json.dumps(record, indent=2) + "\n")
        (self.path / RECORD).chmod(0o600)
        print(f"Listo. Comprueba el sitio y, cuando todo funcione: stack.sh site-rename {row['domain']} --drop-old")
        if not self.was_running:
            print("El sitio estaba detenido y sigue detenido.")
        for path in plan.cron:
            print(f"AVISO: {path} menciona el nombre viejo.")

    def rename_database(self):
        plan, row = self.plan, self.row
        new_row = plan.new_row()
        if row["database"]["mode"] == "container":
            self.step("Arrancando la base dedicada")
            run(compose_args(row) + ["up", "-d", "--no-build", "--wait", "db"])
        self.step(f"Respaldo de {plan.old_db}")
        result = run([sys.executable, str(ROOT / "scripts/backups.py"), "backup", row["domain"]],
                     stdout=subprocess.PIPE, text=True)
        print(result.stdout, end="", flush=True)
        prefix = f"OK {row['domain']}: "
        self.backup = next((l[len(prefix):] for l in result.stdout.splitlines() if l.startswith(prefix)), "")
        if not self.backup:
            raise RuntimeError("No se obtuvo la ruta del respaldo")
        dump = Path(self.backup) / "database.sql.gz"
        self.step(f"Creando {plan.new_db}")
        database.query(row, scaffold.db_sql(new_row, self.password, plan.host))
        self.created_db = True
        self.step("Actualizando site.json, variables y secrets/init.sql")
        write_keep_mode(self.path / "site.json", json.dumps(new_row, indent=2) + "\n")
        rewrite_env(row, plan.old_db, plan.new_db)
        if (self.path / "secrets/init.sql").is_file():
            write_keep_mode(self.path / "secrets/init.sql", scaffold.db_sql(new_row, self.password, plan.host))
        self.step(f"Importando el respaldo en {plan.new_db}")
        backups.scan_sql(new_row, dump)
        backups.load_sql(new_row, dump)
        before, after = self.tables(row, plan.old_db), self.tables(row, plan.new_db)
        if before != after:
            raise RuntimeError(f"La base nueva tiene {after} tablas y la vieja {before}")

    def start(self, row):
        self.step("Arrancando el sitio")
        run(compose_args(row) + ["up", "-d", "--no-build", "--wait"])
        cache = self.path / "app/bootstrap/cache/config.php"
        if row["kind"] == "laravel" and self.plan.rename_db and cache.exists():
            run(compose_args(row) + ["exec", "-T", "--user", "33:33", "web", "php", "artisan", "config:cache"])

    def rollback(self):
        plan = self.plan
        print("ERROR: se vuelve al estado anterior", file=sys.stderr, flush=True)
        try:
            if self.created_db:
                database.query(self.row, f"DROP DATABASE IF EXISTS `{plan.new_db}`; "
                                         f"DROP USER IF EXISTS '{plan.new_db}'@'{plan.host}';")
            if plan.rename_project:
                containers = project_containers(plan.new_project)
                if containers:
                    run(["docker", "rm", "-f", *containers], stdout=subprocess.DEVNULL)
            self.restore_snapshot()
            (self.path / SNAPSHOT).unlink(missing_ok=True)
            if self.was_running:
                run(compose_args(self.row) + ["up", "-d", "--no-build", "--wait"])
        except BaseException as exc:
            print(f"ERROR: la vuelta atrás no terminó ({exc}). Archivos originales en {self.path / SNAPSHOT}",
                  file=sys.stderr, flush=True)
            if self.snapshot:
                (self.path / SNAPSHOT).write_bytes(self.snapshot)


def drop_old(row):
    path = site_path(row) / RECORD
    if not path.is_file():
        raise ValueError("No hay un renombrado pendiente de limpiar para este sitio")
    record = json.loads(path.read_text())
    old_db = record.get("old_database")
    if old_db:
        if not LEGACY_DATABASE.fullmatch(old_db) or row["database"]["name"] == old_db:
            raise ValueError("El sitio todavía usa la base vieja; no se borra")
        print(f"==> Borrando la base y el usuario {old_db}", flush=True)
        database.query(row, f"DROP DATABASE IF EXISTS `{old_db}`; DROP USER IF EXISTS '{old_db}'@'{record['host']}';")
    old_project = record.get("old_project")
    if old_project:
        if not LEGACY_PROJECT.fullmatch(old_project) or project_name(row) == old_project:
            raise ValueError("El sitio todavía usa el proyecto viejo; no se borra")
        if project_containers(old_project):
            raise ValueError(f"Hay contenedores del proyecto {old_project}; revísalos antes")
        # Compose nombra las imágenes que construye PROYECTO-SERVICIO. Se quitan por nombre, no por ID: la imagen
        # nueva suele ser la misma (otro tag), y borrar el ID afectaría a los contenedores en marcha.
        images = output(["docker", "image", "ls", "--filter", f"reference={old_project}-*",
                         "--format", "{{.Repository}}:{{.Tag}}"]).split()
        if images:
            print(f"==> Quitando las imágenes de {old_project}", flush=True)
            run(["docker", "image", "rm", *images], stdout=subprocess.DEVNULL)
        label = f"label=com.docker.compose.project={old_project}"
        for network in output(["docker", "network", "ls", "-q", "--filter", label]).split():
            run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL)
    path.unlink()
    (site_path(row) / SNAPSHOT).unlink(missing_ok=True)
    print("Listo.")
    for name in record.get("volumes", []):
        print(f"El volumen {name} sigue en uso (external) y no se toca.")
    if record.get("backup"):
        print(f"El respaldo previo sigue en {record['backup']}.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("domain")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="ejecutar el plan (corte breve del sitio)")
    action.add_argument("--drop-old", action="store_true", help="borrar la base, el usuario, las imágenes y las redes viejas")
    p.add_argument("--containers-only", action="store_true", help="renombrar solo el proyecto de Compose")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    os.umask(0o077)
    row = site(args.domain)
    with (site_path(row) / ".deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Hay un despliegue en curso para este sitio") from None
        if args.drop_old:
            drop_old(row)
            return 0
        if (site_path(row) / RECORD).exists():
            raise ValueError("Ya se renombró este sitio; limpia lo viejo con --drop-old")
        plan = Plan(row, args.containers_only)
        print(plan.describe())
        if plan.empty:
            return 0
        if not args.apply:
            print(f"\nNo se cambió nada. Para aplicarlo (corte breve): stack.sh site-rename {row['domain']} --apply"
                  + (" --containers-only" if args.containers_only else ""))
            return 0
        Rename(plan).execute()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: {exc.cmd[0]} terminó con código {exc.returncode}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, StopIteration) as exc:
        print(f"ERROR: {exc or 'sitio no registrado'}", file=sys.stderr)
        sys.exit(1)
