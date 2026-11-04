#!/usr/bin/env python3
"""Tareas de aplicación: composer y despliegue. Se invoca como root desde stack.sh y deploy.sh."""
import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from registry import ROOT, compose_args, settings, site
import releases

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
    if path.is_symlink():
        raise ValueError("app/ apunta a un release, que no se modifica en su lugar; usa deploy.sh")
    if not path.is_dir():
        raise ValueError(f"{path} no es un directorio")
    return path


def release_mounts(release, writable=False):
    """Monta un release concreto en lugar del activo (run -v reemplaza el montaje del mismo destino)."""
    return ["-v", f"{release}:/var/www/html{'' if writable else ':ro'}",
            "-v", f"{release}/bootstrap/cache:/var/www/html/bootstrap/cache"]


def fix_owner(row, release=None):
    """Cede a 33:33 los directorios escribibles de Laravel, sin seguir enlaces simbólicos."""
    if release is None:
        app = app_dir(row)
        folders = [app / name for name in LARAVEL_WRITABLE]
    else:
        folders = [ROOT / "sites" / row["domain"] / "shared/storage", Path(release) / "bootstrap/cache"]
    for folder in folders:
        if folder.is_symlink():
            raise ValueError(f"{folder} es un enlace simbólico; no se cambia su propietario")
        folder.mkdir(parents=True, exist_ok=True)
        for current, dirs, files in os.walk(folder):
            for entry in [current, *(os.path.join(current, n) for n in dirs + files)]:
                os.chown(entry, 33, 33, follow_symlinks=False)
                if not os.path.islink(entry):
                    mode = os.stat(entry).st_mode & 0o7777
                    os.chmod(entry, mode | (0o700 if os.path.isdir(entry) else 0o600))


def composer_plan(row, args, service="web", release=None):
    """Devuelve los pasos: ('run', comando) o ('chown', release). Separado para poder probarlo.

    Con release, composer trabaja sobre ese directorio (un release en construcción) y no sobre app/."""
    kind = row["kind"]
    if kind == "static":
        raise ValueError("Un sitio estático no utiliza composer")
    args = list(args) or list(COMPOSER_DEFAULT)
    action = args[0]
    base = compose_args(row) + ["run", "--rm", "--no-deps", "-e", "COMPOSER_HOME=/tmp/composer"]
    read_mounts = release_mounts(release) if release else []
    if action in COMPOSER_READ:
        return [("run", base + read_mounts + ["--user", "33:33", service, "composer", *args])]
    if action not in COMPOSER_WRITE:
        raise ValueError(f"Subcomando de composer no admitido: {action}")
    app = release or app_dir(row)
    cache = COMPOSER_CACHE / row["id"]
    # Root del contenedor no tiene DAC_OVERRIDE: escribe app/ (de root) pero no lee storage/ (33:33).
    # Por eso en Laravel los scripts se ejecutan después, como 33:33.
    mounts = release_mounts(release, writable=True) if release else ["-v", f"{app}:/var/www/html"]
    write = base + ["--user", "0:0", "-e", "COMPOSER_ALLOW_SUPERUSER=1", "-e", "COMPOSER_CACHE_DIR=/composer-cache",
                    *mounts, "-v", f"{cache}:/composer-cache", service, "composer", *args]
    if kind != "laravel":
        return [("cache", cache), ("run", write)]
    if "--no-scripts" not in args:
        write.append("--no-scripts")
    steps = [("cache", cache), ("run", write), ("chown", release)]
    if "--no-scripts" not in args:
        # post-autoload-dump ejecuta package:discover y solo necesita escribir bootstrap/cache.
        steps.append(("run", base + read_mounts + ["--user", "33:33", service, "composer", "run-script",
                                                   "post-autoload-dump", "--no-interaction"]))
    return steps


def apply(row, steps):
    for kind, value in steps:
        if kind == "cache":
            value.mkdir(parents=True, mode=0o700, exist_ok=True)
        elif kind == "chown":
            fix_owner(row, value)
        else:
            run(value)


def composer(row, args, service="web", verbose=False):
    build_quiet(row, verbose)
    # post-autoload-dump de Laravel borra bootstrap/cache/config.php: se regenera si existía.
    steps = composer_plan(row, args, service)
    cached = row["kind"] == "laravel" and not releases.enabled(row) and \
        (app_dir(row) / "bootstrap/cache/config.php").exists()
    apply(row, steps)
    if cached and steps[-1][1][-3:-1] == ["run-script", "post-autoload-dump"]:
        run(compose_args(row) + ["run", "--rm", "--no-deps", "--user", "33:33", service, "php", "artisan", "optimize"])
    if row["kind"] == "laravel" and args and args[0] in ("update", "require", "remove"):
        print("Nota: post-update-cmd no se ejecuta (el código es de solo lectura para 33:33). "
              "Publica los assets de paquetes durante la construcción o el despliegue.")


# --- Despliegue en sitio (app/ es un directorio) ------------------------------------------

HOOKS = ("pre-deploy", "post-deploy")
# Rutas que el despliegue nunca toca: datos, caché de Laravel, dependencias y secretos.
EXCLUDES = {
    "laravel": ["/.env", "/.git/", "/storage/", "/bootstrap/cache/", "/public/storage", "/node_modules/"],
    "php": ["/.env", "/.git/", "/node_modules/"],
}


class DeployError(RuntimeError):
    pass


def artisan(row, service, *args, deps=False):
    command = compose_args(row) + ["run", "--rm"] + ([] if deps else ["--no-deps"])
    return command + ["--user", "33:33", service, "php", "artisan", *args]


def running_services(row):
    result = subprocess.run(compose_args(row) + ["ps", "--status", "running", "--services"],
                            capture_output=True, text=True, check=True)
    return result.stdout.split()


def app_services(row):
    """Servicios definidos en el Compose (incluidos los overlays)."""
    result = subprocess.run(compose_args(row) + ["config", "--services"], capture_output=True, text=True, check=True)
    return result.stdout.split()


def services_to_recreate(defined, running, service):
    """web, los workers de DockerWS y lo que ya corría; nunca db ni servicios detenidos a propósito
    (por ejemplo, un servicio que solo construye la imagen)."""
    workers = {"scheduler"}
    return [s for s in defined if s != "db" and (s == service or s in workers or s in running)]


def hook(row, name):
    """Ganchos del host: solo archivos de root, no escribibles por otros, fuera del código desplegado."""
    folder = ROOT / "sites" / row["domain"] / "hooks"
    path = folder / name
    if not path.exists() and not path.is_symlink():
        return None
    for item in (folder, path):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise DeployError(f"Gancho inseguro {item}: debe ser de root, sin enlaces y sin escritura de grupo/otros")
    if not stat.S_ISREG(path.lstat().st_mode) or not os.access(path, os.X_OK):
        raise DeployError(f"Gancho {path} debe ser un archivo ejecutable")
    return path


def run_hook(row, name, env):
    path = hook(row, name)
    if path:
        print(f"==> Gancho {name}", flush=True)
        run([str(path)], cwd=ROOT / "sites" / row["domain"], env={**os.environ, **env})


def check_source(row, source):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise DeployError(f"El origen debe ser un directorio, no un enlace: {source}")
    source = source.resolve()
    app = (ROOT / "sites" / row["domain"] / "app").resolve()
    if source == app or app in source.parents or source in app.parents:
        raise DeployError("El origen no puede ser app/ ni contenerlo")
    if row["kind"] == "laravel" and not ((source / "artisan").is_file() and (source / "composer.json").is_file()):
        raise DeployError("El origen no parece un proyecto Laravel (faltan artisan o composer.json)")
    if not any(source.iterdir()):
        raise DeployError("El origen está vacío; no se sincroniza para no borrar app/")
    return source


def rsync_command(row, source, composer_runs, dest=None):
    """Sincroniza hacia app/ (con --delete) o hacia un release nuevo y vacío (dest)."""
    excludes = list(EXCLUDES[row["kind"]]) + ["/" + releases.META]
    if composer_runs:
        excludes.append("/vendor/")
    # Sin -o/-g: los archivos quedan de root. go-w,go+rX: legibles por 33, escribibles solo por root.
    command = ["rsync", "-rlpt", "--chmod=go-w,go+rX"] + ([] if dest else ["--delete"])
    command += [f"--exclude={item}" for item in excludes]
    return command + [f"{source}/", f"{dest or ROOT / 'sites' / row['domain'] / 'app'}/"]


def git(app, *args, capture=False):
    command = ["git", "-c", f"safe.directory={app}", "-C", str(app), *args]
    if capture:
        return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    return run(command)


def resolve_ref(row, ref):
    app = app_dir(row)
    if not (app / ".git").exists():
        raise DeployError("--ref requiere que app/ sea un checkout de git")
    if git(app, "status", "--porcelain", "--untracked-files=no", capture=True):
        raise DeployError("app/ tiene cambios locales en archivos versionados; resuélvelos antes de desplegar")
    git(app, "fetch", "--prune", "--tags", "origin")
    for candidate in (f"refs/remotes/origin/{ref}", ref):
        result = subprocess.run(["git", "-c", f"safe.directory={app}", "-C", str(app), "rev-parse", "--verify",
                                 "--quiet", candidate + "^{commit}"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    raise DeployError(f"No existe la referencia {ref}")


def link_public_storage(row, folder=None, storage=None):
    """storage:link no puede escribir en el código montado de solo lectura: se crea desde el host.

    Con releases, el release tiene storage/ vacío (punto de montaje) y el destino real es shared/storage."""
    app = Path(folder) if folder else app_dir(row)
    storage = Path(storage) if storage else app / "storage"
    link, target = app / "public/storage", "../storage/app/public"
    if (storage / "app/public").is_dir() and (app / "public").is_dir() and not link.exists() and not link.is_symlink():
        link.symlink_to(target)


def sudo_uid():
    """UID de quien invocó con sudo, o None si es root directamente."""
    uid = os.environ.get("SUDO_UID", "")
    return int(uid) if uid.isdigit() and int(uid) != 0 else None


def incoming_dir(row, uid, root_uid=0):
    """Directorio de subida del usuario de despliegue: base de root y carpeta del sitio propia del usuario."""
    base = Path(settings()["deploy_incoming_dir"])
    folder = base / row["domain"]
    for item, owner in ((base, root_uid), (folder, uid)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            raise DeployError(f"No existe {item}; ejecuta host/install-deploy-user.sh") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != owner:
            raise DeployError(f"{item} debe ser un directorio (sin enlaces) de UID {owner}")
        if owner == root_uid and info.st_mode & 0o022:
            raise DeployError(f"{item} no debe ser escribible por grupo u otros")
    return folder


def restrict_for_sudo(row, uid, source, ref, backup, root_uid=0):
    """Un usuario de despliegue solo puede publicar desde su carpeta de subida y no puede omitir el respaldo."""
    if not backup:
        raise DeployError("--skip-backup solo está permitido para root")
    folder = incoming_dir(row, uid, root_uid)
    if source and Path(source).resolve() != folder.resolve():
        raise DeployError(f"Con sudo, el origen debe ser {folder}")
    return None if ref else str(folder)


class Deployment:
    def __init__(self, row, source=None, ref=None, backup=True, migrate=True, composer=True,
                 service="web", verbose=False):
        if row["kind"] not in EXCLUDES:
            raise DeployError("deploy.sh admite sitios laravel y php")
        if source and ref:
            raise DeployError("Usa --from o --ref, no ambos")
        self.row, self.source, self.ref = row, source, ref
        self.backup, self.migrate, self.composer = backup, migrate, composer
        self.service, self.verbose = service, verbose
        self.laravel = row["kind"] == "laravel"
        self.maintenance = False
        self.running = []
        self.backup_path = ""

    def step(self, text):
        print(f"==> {text}", flush=True)

    def prepare(self):
        """Todo lo que puede fallar sin afectar al sitio en línea."""
        if self.source:
            self.source = check_source(self.row, self.source)
        if self.ref:
            self.step(f"Obteniendo {self.ref}")
            self.commit = resolve_ref(self.row, self.ref)
        app = app_dir(self.row)
        if self.composer and not self.source and not (app / "composer.json").exists() and not self.laravel:
            self.composer = False
        self.step("Construyendo la imagen")
        build_quiet(self.row, self.verbose)
        self.env = {"DOCKERWS_SITE": self.row["domain"], "DOCKERWS_APP": str(app),
                    "DOCKERWS_SOURCE": str(self.source or ""), "DOCKERWS_REF": self.ref or ""}
        run_hook(self.row, "pre-deploy", self.env)

    def enter_maintenance(self):
        self.running = running_services(self.row)
        if self.laravel and self.service in self.running:
            self.step("Modo mantenimiento (artisan down)")
            run(compose_args(self.row) + ["exec", "-T", "--user", "33:33", self.service,
                                          "php", "artisan", "down", "--retry=60"])
            self.maintenance = True

    def take_backup(self):
        if self.backup and self.row["database"]["engine"] != "none":
            self.step("Respaldo de la base del sitio")
            result = run([sys.executable, str(ROOT / "scripts/backups.py"), "backup", self.row["domain"]],
                         stdout=subprocess.PIPE, text=True)
            output = getattr(result, "stdout", None) or ""
            print(output, end="", flush=True)
            prefix = f"OK {self.row['domain']}: "
            # La ruta queda en los metadatos del release para sugerirla si hay que volver atrás.
            self.backup_path = next((l[len(prefix):] for l in output.splitlines() if l.startswith(prefix)), "")

    def sync(self):
        if self.source:
            self.step(f"Sincronizando código desde {self.source}")
            composer_runs = self.composer and (self.source / "composer.json").exists()
            run(rsync_command(self.row, self.source, composer_runs))
        elif self.ref:
            self.step(f"Checkout de {self.commit[:12]}")
            git(app_dir(self.row), "-c", "advice.detachedHead=false", "checkout", "--force", "--detach", self.commit)
        if self.laravel:
            fix_owner(self.row)
            link_public_storage(self.row)

    def dependencies(self):
        if self.composer and (app_dir(self.row) / "composer.json").exists():
            self.step("Dependencias (composer)")
            apply(self.row, composer_plan(self.row, [], self.service))

    def laravel_tasks(self):
        if not self.laravel:
            return
        if self.migrate:
            self.step("Migraciones")
            run(artisan(self.row, self.service, "migrate", "--force", deps=True))
        self.step("Cachés de Laravel (optimize)")
        run(artisan(self.row, self.service, "optimize", deps=True))

    def restart(self):
        # Contenedores nuevos: OPcache vacío y workers con el código nuevo.
        services = services_to_recreate(app_services(self.row), self.running, self.service)
        self.step("Recreando " + ", ".join(services))
        run(compose_args(self.row) + ["up", "-d", "--no-build", "--force-recreate", "--wait", *services])

    def leave_maintenance(self):
        if self.laravel:
            self.step("Fin del mantenimiento (artisan up)")
            run(compose_args(self.row) + ["exec", "-T", "--user", "33:33", self.service, "php", "artisan", "up"])
            self.maintenance = False

    def execute(self):
        self.prepare()
        try:
            self.enter_maintenance()
            self.take_backup()
            self.sync()
            self.dependencies()
            self.laravel_tasks()
            self.restart()
            self.leave_maintenance()
        except BaseException:
            if self.maintenance:
                print(f"El sitio quedó en mantenimiento. Corrige el problema y vuelve a desplegar, o ejecuta: "
                      f"stack.sh site {self.row['domain']} exec {self.service} php artisan up", file=sys.stderr)
            raise
        run_hook(self.row, "post-deploy", self.env)
        print(f"Despliegue terminado: {self.row['domain']}")


# --- Releases atómicos ----------------------------------------------------------------------

APPLIED_PHP = (
    'require "vendor/autoload.php"; $app = require "bootstrap/app.php"; '
    '$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap(); '
    '$c = config("database.migrations"); $t = is_array($c) ? ($c["table"] ?? "migrations") : ($c ?: "migrations"); '
    'echo "DOCKERWS:", json_encode(Illuminate\\Support\\Facades\\DB::table($t)->pluck("migration")), PHP_EOL;')


def release_artisan(row, service, release, *args, deps=False):
    """artisan con un release concreto montado (antes de activarlo), como 33:33."""
    command = compose_args(row) + ["run", "--rm"] + ([] if deps else ["--no-deps"])
    return command + release_mounts(release) + ["--user", "33:33", service, "php", "artisan", *args]


def history(row, text):
    """Registro de activaciones y vueltas atrás, con quién las hizo."""
    who = os.environ.get("SUDO_USER") or "root"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (releases.releases_dir(row) / "history.log").open("a") as log:
        log.write(f"{stamp} {who} {text}\n")


def repo_dir(row):
    return ROOT / "sites" / row["domain"] / "repo.git"


def resolve_mirror_ref(row, ref):
    repo = repo_dir(row)
    if not repo.is_dir():
        raise DeployError("--ref con releases requiere un mirror: git clone --mirror URL sites/DOMINIO/repo.git")
    git(repo, "fetch", "--prune", "origin")
    for candidate in (f"refs/heads/{ref}", f"refs/tags/{ref}", ref):
        result = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), "rev-parse", "--verify",
                                 "--quiet", candidate + "^{commit}"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    raise DeployError(f"No existe la referencia {ref}")


def export_commit(repo, commit, dest):
    archive = subprocess.Popen(["git", "-c", f"safe.directory={repo}", "-C", str(repo), "archive", "--format=tar", commit],
                               stdout=subprocess.PIPE)
    try:
        subprocess.run(["tar", "-x", "--no-same-owner", "-C", str(dest)], stdin=archive.stdout, check=True)
    finally:
        archive.stdout.close()
        if archive.wait():
            raise DeployError("git archive falló")


class ReleaseDeployment(Deployment):
    """Construye releases/FECHA sin tocar el sitio, migra con él y lo activa cambiando el enlace app."""

    def __init__(self, row, assets=False, maintenance=True, **options):
        super().__init__(row, **options)
        self.assets, self.use_maintenance = assets, maintenance
        self.pending = self.final = None
        self.db_touched = self.switched = False
        self.commit = ""

    def prepare(self):
        row = self.row
        releases.clean_pending(row)
        self.active = releases.current(row)
        active_dir = releases.path(row, self.active)
        if self.source:
            self.source = check_source(row, self.source)
        if self.ref:
            self.step(f"Obteniendo {self.ref}")
            self.commit = resolve_mirror_ref(row, self.ref)
        self.pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=releases.releases_dir(row)))
        self.pending.chmod(0o755)
        self.step("Preparando el release")
        if self.ref:
            export_commit(repo_dir(row), self.commit, self.pending)
        else:
            # Sin origen se reconstruye a partir del release activo (vendor/ se regenera con composer).
            origin = self.source or active_dir
            run(rsync_command(row, origin, self.composer and (origin / "composer.json").exists(), self.pending))
        self.normalize()
        self.step("Construyendo la imagen")
        build_quiet(row, self.verbose)
        if self.composer and (self.pending / "composer.json").exists():
            self.step("Dependencias (composer)")
            apply(row, composer_plan(row, [], self.service, release=self.pending))
        if self.assets:
            self.build_assets()
        self.env = {"DOCKERWS_SITE": row["domain"], "DOCKERWS_APP": str(self.pending),
                    "DOCKERWS_SOURCE": str(self.source or ""), "DOCKERWS_REF": self.ref or ""}
        run_hook(row, "pre-deploy", self.env)

    def normalize(self):
        """El release nunca lleva secretos ni datos: storage/ queda vacío como punto de montaje."""
        release = self.pending
        (release / ".env").unlink(missing_ok=True)
        if (release / ".git").exists():
            shutil.rmtree(release / ".git")
        if self.laravel:
            if (release / "storage").exists() or (release / "storage").is_symlink():
                if (release / "storage").is_symlink():
                    (release / "storage").unlink()
                else:
                    shutil.rmtree(release / "storage")
            (release / "storage").mkdir(mode=0o755)
            (release / "bootstrap/cache").mkdir(parents=True, exist_ok=True)
            fix_owner(self.row, release)
            link_public_storage(self.row, release, ROOT / "sites" / self.row["domain"] / "shared/storage")

    def build_assets(self):
        self.step("Assets (npm ci && npm run build)")
        # Root sin capabilities: escribe el release (de root) pero nada más del host.
        run(["docker", "run", "--rm", "--user", "0:0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
             "-v", f"{self.pending}:/app", "-w", "/app", settings().get("node_image", "node:22-bookworm-slim"),
             "sh", "-c", "npm ci && npm run build"])
        if (self.pending / "node_modules").exists():
            shutil.rmtree(self.pending / "node_modules")

    def enter_maintenance(self):
        if self.use_maintenance:
            super().enter_maintenance()
        else:
            # Las migraciones deben ser compatibles con el código en línea (expand/contract).
            self.running = running_services(self.row)
            self.step("Sin modo mantenimiento (--no-maintenance)")

    def leave_maintenance(self):
        if self.maintenance:
            super().leave_maintenance()

    def laravel_tasks(self):
        if not self.laravel:
            return
        if self.migrate:
            self.step("Migraciones (con el release nuevo, aún sin activar)")
            self.db_touched = True
            run(release_artisan(self.row, self.service, self.pending, "migrate", "--force", deps=True))
        self.step("Cachés de Laravel (optimize) en el release nuevo")
        run(release_artisan(self.row, self.service, self.pending, "optimize", deps=True))

    def finalize(self):
        self.name = releases.new_name(self.row)
        releases.write_meta(self.pending, {
            "name": self.name, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": str(self.source or ""), "ref": self.ref or "", "commit": self.commit,
            "previous": self.active, "backup": self.backup_path,
            "migrations": releases.migration_files(self.pending)})
        self.final = releases.releases_dir(self.row) / self.name
        os.rename(self.pending, self.final)
        self.pending = None

    def activate(self):
        self.step(f"Activando {self.name}")
        releases.switch(self.row, self.name)
        self.switched = True
        history(self.row, f"deploy {self.active} -> {self.name}")

    def discard(self):
        for folder in (self.pending, self.final):
            if folder and folder.exists():
                shutil.rmtree(folder)

    def recover(self):
        domain = self.row["domain"]
        if self.switched:
            print(f"El release {self.name} quedó activo, pero el despliegue falló después. El sitio sigue en "
                  f"mantenimiento. Vuelve atrás con: deploy.sh {domain} --rollback", file=sys.stderr)
            return
        self.discard()
        if self.maintenance and not self.db_touched:
            try:
                self.leave_maintenance()
                print(f"Despliegue cancelado; el sitio sigue en línea con {self.active}.", file=sys.stderr)
            except subprocess.CalledProcessError:
                pass
        elif self.maintenance:
            hint = f" o restaura {self.backup_path}" if self.backup_path else ""
            print(f"Las migraciones pudieron quedar a medias: el sitio sigue en mantenimiento con {self.active}. "
                  f"Corrige y vuelve a desplegar{hint}.", file=sys.stderr)

    def execute(self):
        try:
            self.prepare()
        except BaseException:
            self.discard()
            raise
        try:
            self.enter_maintenance()
            self.take_backup()
            self.laravel_tasks()
            self.finalize()
            self.activate()
            self.restart()
            self.leave_maintenance()
        except BaseException:
            self.recover()
            raise
        self.env["DOCKERWS_APP"] = str(self.final)
        run_hook(self.row, "post-deploy", self.env)
        removed = releases.prune(self.row, int(settings().get("keep_releases", 5)))
        if removed:
            print("Releases eliminados: " + ", ".join(removed))
        print(f"Despliegue terminado: {self.row['domain']} ({self.name})")


def applied_migrations(row, service):
    result = subprocess.run(compose_args(row) + ["run", "--rm", "--user", "33:33", service, "php", "-r", APPLIED_PHP],
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("DOCKERWS:"):
            return json.loads(line[len("DOCKERWS:"):])
    raise DeployError("No se pudo leer la tabla de migraciones; revisa la conexión a la base")


class Rollback(Deployment):
    """Vuelve a un release anterior. No revierte migraciones: se niega si el destino no las conoce."""

    def __init__(self, row, target=None, force=False, service="web", verbose=False):
        super().__init__(row, service=service, verbose=verbose, backup=False)
        self.active = releases.current(row)
        self.target = target or releases.previous(row)
        self.folder = releases.path(row, self.target)
        if self.target == self.active:
            raise DeployError(f"{self.target} ya es el release activo")
        self.force = force

    def check_migrations(self):
        if not self.laravel:
            return
        meta = releases.read_meta(self.row, self.target)
        known = meta["migrations"] if isinstance(meta.get("migrations"), list) else releases.migration_files(self.folder)
        unknown = releases.unknown_migrations(applied_migrations(self.row, self.service), known)
        if not unknown:
            return
        listed = ", ".join(unknown[:10]) + (f" y {len(unknown) - 10} más" if len(unknown) > 10 else "")
        if self.force:
            print(f"AVISO: --force con migraciones que {self.target} no conoce: {listed}", file=sys.stderr)
            return
        backup = releases.read_meta(self.row, self.active).get("backup")
        raise DeployError(
            f"La base tiene migraciones que {self.target} no conoce: {listed}. El código anterior podría fallar o "
            f"dañar datos. Si las migraciones son compatibles, root puede usar --force; si no, restaura el respaldo "
            f"previo al despliegue" + (f" ({backup})" if backup else "") + " y luego vuelve atrás.")

    def execute(self):
        self.step(f"Vuelta atrás: {self.active} -> {self.target}")
        self.check_migrations()
        build_quiet(self.row, self.verbose)
        try:
            self.enter_maintenance()
            if self.laravel:
                # La caché de configuración del release anterior se generó con el entorno de entonces.
                self.step(f"Cachés de Laravel (optimize) en {self.target}")
                fix_owner(self.row, self.folder)
                run(release_artisan(self.row, self.service, self.folder, "optimize", deps=True))
            releases.switch(self.row, self.target)
            history(self.row, f"rollback {self.active} -> {self.target}")
            self.restart()
            self.leave_maintenance()
        except BaseException:
            if self.maintenance:
                print(f"La vuelta atrás falló; el sitio quedó en mantenimiento con el release "
                      f"{releases.current(self.row)}.", file=sys.stderr)
            raise
        print(f"Vuelta atrás terminada: {self.row['domain']} ({self.target})")


def recreate(row, service, running):
    run(compose_args(row) + ["up", "-d", "--no-build", "--force-recreate", "--wait",
                             *services_to_recreate(app_services(row), running, service)])


def init_releases(row, service="web"):
    """Convierte app/ en releases/FECHA + enlace app, y storage/ en shared/storage. Reversible."""
    import workers
    if row["kind"] not in EXCLUDES:
        raise DeployError("Los releases admiten sitios laravel y php")
    site_path = ROOT / "sites" / row["domain"]
    app, rel_root, shared = site_path / "app", site_path / "releases", site_path / "shared"
    if releases.enabled(row):
        raise DeployError("El sitio ya usa releases")
    if app.is_symlink() or not app.is_dir():
        raise DeployError("app/ debe ser un directorio")
    if (rel_root.exists() and any(rel_root.iterdir())) or (shared / "storage").exists():
        raise DeployError("Ya existen releases/ o shared/storage; revísalos antes de inicializar")
    laravel = row["kind"] == "laravel"
    config = workers.resolved(row)  # Antes de mover nada: rutas de app/storage aún válidas.
    deployment = Deployment(row, service=service)
    deployment.step("Inicializando releases")
    undo = []
    try:
        deployment.enter_maintenance()
        rel_root.mkdir(mode=0o755, exist_ok=True)
        name = releases.new_name(row)
        release = rel_root / name
        os.rename(app, release)
        undo.append(lambda: os.rename(release, app))
        if laravel:
            shared.mkdir(mode=0o755, exist_ok=True)
            os.rename(release / "storage", shared / "storage")
            undo.append(lambda: os.rename(shared / "storage", release / "storage"))
            (release / "storage").mkdir(mode=0o755)
            undo.append(lambda: (release / "storage").rmdir())
            link_public_storage(row, release, shared / "storage")
        releases.switch(row, name)
        undo.append(lambda: (site_path / "app").unlink())
        if laravel:
            overlay = releases.storage_overlay(config, row)
            if overlay:
                target = site_path / "compose.d" / releases.OVERLAY
                workers.write_overlay(target, json.dumps(overlay, indent=2) + "\n")
                undo.append(lambda: target.unlink())
            releases.rewrite_worker_storage(row, releases.APP_STORAGE, releases.SHARED_STORAGE)
            undo.append(lambda: releases.rewrite_worker_storage(row, releases.SHARED_STORAGE, releases.APP_STORAGE))
        releases.write_meta(release, {"name": name, "initial": True, "previous": None,
                                      "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                      "migrations": releases.migration_files(release)})
        deployment.step("Recreando servicios con el release inicial")
        recreate(row, service, deployment.running)
        deployment.leave_maintenance()
    except BaseException:
        for action in reversed(undo):
            try:
                action()
            except OSError as exc:
                print(f"No se pudo deshacer un paso: {exc}", file=sys.stderr)
        print("Inicialización cancelada; se restituyó app/. Si el sitio quedó en mantenimiento: "
              f"stack.sh site {row['domain']} exec {service} php artisan up", file=sys.stderr)
        raise
    history(row, f"init {name}")
    if (release / ".git").is_dir() and not repo_dir(row).exists():
        print(f"Nota: app/ era un checkout de git. Para --ref con releases crea el mirror:\n"
              f"  git clone --mirror URL_DEL_REPOSITORIO {repo_dir(row)}")
    print(f"Releases inicializados: app -> releases/{name}")


def revert_releases(row, service="web"):
    """Vuelve a la estructura anterior: el release activo pasa a ser app/ y shared/storage, app/storage."""
    site_path = ROOT / "sites" / row["domain"]
    active = releases.current(row)
    release = releases.path(row, active)
    laravel = row["kind"] == "laravel"
    if laravel and any((release / "storage").iterdir()):
        raise DeployError(f"releases/{active}/storage no está vacío; revísalo antes de revertir")
    deployment = Deployment(row, service=service)
    deployment.step(f"Revirtiendo releases (activo: {active})")
    deployment.enter_maintenance()
    overlay = site_path / "compose.d" / releases.OVERLAY
    if laravel:
        overlay.unlink(missing_ok=True)
        releases.rewrite_worker_storage(row, releases.SHARED_STORAGE, releases.APP_STORAGE)
    (site_path / "app").unlink()
    os.rename(release, site_path / "app")
    if laravel:
        (site_path / "app/storage").rmdir()
        os.rename(site_path / "shared/storage", site_path / "app/storage")
    (site_path / "app" / releases.META).unlink(missing_ok=True)
    deployment.step("Recreando servicios")
    recreate(row, service, deployment.running)
    deployment.leave_maintenance()
    print(f"app/ vuelve a ser un directorio. Los demás releases siguen en {site_path / 'releases'}; "
          "bórralos cuando ya no los necesites.")


def deploy(row, rollback=None, init=False, revert=False, force=False, assets=False, maintenance=True, **options):
    uid = sudo_uid()
    if force and rollback is None:
        raise DeployError("--force solo se usa con --rollback")
    if uid is not None:
        if init or revert or force:
            raise DeployError("--init-releases, --revert-releases y --force solo están permitidos para root")
        if rollback is not None:
            if not settings().get("deploy_user_rollback", True):
                raise DeployError("La vuelta atrás con sudo está desactivada (deploy_user_rollback en config/local.json)")
        else:
            options["source"] = restrict_for_sudo(row, uid, options.get("source"), options.get("ref"),
                                                  options.get("backup", True))
    lock_path = ROOT / "sites" / row["domain"] / ".deploy.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeployError("Ya hay un despliegue en curso para este sitio")
        service, verbose = options.get("service", "web"), options.get("verbose", False)
        if init:
            init_releases(row, service)
        elif revert:
            revert_releases(row, service)
        elif rollback is not None:
            Rollback(row, rollback or None, force, service, verbose).execute()
        elif releases.enabled(row):
            ReleaseDeployment(row, assets=assets, maintenance=maintenance, **options).execute()
        elif assets or not maintenance:
            raise DeployError("--assets y --no-maintenance requieren releases: sin ellos el código en uso se "
                              "modifica en su lugar")
        else:
            Deployment(row, **options).execute()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="action", required=True)
    c = commands.add_parser("composer", help="composer con el procedimiento seguro para Laravel")
    c.add_argument("domain")
    c.add_argument("--service", default="web")
    c.add_argument("--verbose", action="store_true")
    c.add_argument("args", nargs=argparse.REMAINDER)
    d = commands.add_parser("deploy", help="desplegar código en el sitio")
    d.add_argument("domain")
    origin = d.add_mutually_exclusive_group()
    origin.add_argument("--from", dest="source", metavar="DIR", help="directorio con el código ya subido")
    origin.add_argument("--ref", help="rama, etiqueta o commit (checkout en app/ o mirror repo.git con releases)")
    origin.add_argument("--rollback", nargs="?", const="", metavar="RELEASE", help="volver al release anterior o al indicado")
    origin.add_argument("--init-releases", action="store_true", help="convertir el sitio a releases atómicos")
    origin.add_argument("--revert-releases", action="store_true", help="volver a app/ como directorio")
    d.add_argument("--force", action="store_true", help="con --rollback: aceptar migraciones desconocidas (solo root)")
    d.add_argument("--assets", action="store_true", help="con releases: npm ci && npm run build en el release")
    d.add_argument("--no-maintenance", action="store_true",
                   help="con releases: sin artisan down (migraciones compatibles con el código en línea)")
    d.add_argument("--skip-backup", action="store_true")
    d.add_argument("--no-migrate", action="store_true")
    d.add_argument("--no-composer", action="store_true")
    d.add_argument("--service", default="web")
    d.add_argument("--verbose", action="store_true")
    b = commands.add_parser("build", help="construir imágenes mostrando la salida solo si falla")
    b.add_argument("domain")
    b.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    os.umask(0o022)
    row = site(args.domain)
    if args.action == "deploy":
        deploy(row, rollback=args.rollback, init=args.init_releases, revert=args.revert_releases, force=args.force,
               assets=args.assets, maintenance=not args.no_maintenance, source=args.source, ref=args.ref, backup=not args.skip_backup,
               migrate=not args.no_migrate, composer=not args.no_composer, service=args.service, verbose=args.verbose)
    elif args.action == "build":
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
