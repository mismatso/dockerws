#!/usr/bin/env python3
"""Servicios auxiliares de Laravel (scheduler y, a futuro, colas) como overlays de Compose.

Cada worker se deriva del servicio web: misma imagen, entorno, usuario, montajes y
restricciones; sin alias de red, puertos ni healthcheck HTTP. Se escribe en
compose.d/50-NOMBRE.yaml, de modo que compose.yaml nunca se reescribe.
"""
import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from registry import ROOT, compose_args, settings, site

# Definiciones de workers. Para colas bastará agregar, por ejemplo:
#   "queue": {"command": ["php", "artisan", "queue:work", "--sleep=3", "--tries=3", "--max-time=3600"],
#             "memory": "queue_memory"}
WORKERS = {
    "scheduler": {"command": ["php", "artisan", "schedule:work"], "memory": "scheduler_memory"},
}
# Claves de web que el worker hereda tal cual. Todo lo demás se descarta a propósito:
# build, ports, expose, healthcheck, command, entrypoint, sysctls, container_name, labels.
INHERIT = ("user", "read_only", "init", "security_opt", "cap_drop", "env_file", "environment", "volumes",
           "tmpfs", "logging", "pids_limit", "working_dir", "extra_hosts", "dns", "ulimits", "secrets")
CRON_DIR = Path("/etc/cron.d")
CRON_RETIRED = Path("/var/lib/dockerws/cron-retirado")
CRON_WINDOW = (5, 40)  # Segundos del minuto en que se hace el traspaso.


def overlay_path(row, name):
    return ROOT / "sites" / row["domain"] / "compose.d" / f"50-{name}.yaml"


def relativize(value, site_dir):
    """Convierte rutas absolutas dentro del sitio (salida de compose config) en ./relativas."""
    prefix = str(site_dir).rstrip("/") + "/"
    if isinstance(value, str):
        if value == prefix.rstrip("/"):
            return "."
        return "./" + value[len(prefix):] if value.startswith(prefix) else value
    if isinstance(value, list):
        return [relativize(v, site_dir) for v in value]
    if isinstance(value, dict):
        return {k: relativize(v, site_dir) if k in ("source", "path") else v for k, v in value.items()}
    return value


def derive(web, project, name, cfg, web_service="web", site_dir=None):
    spec = WORKERS[name]
    service = {key: web[key] for key in INHERIT if key in web}
    if site_dir is not None:
        for key in ("volumes", "env_file"):
            if key in service:
                service[key] = relativize(service[key], site_dir)
    if "image" in web:
        service["image"] = web["image"]
    if "build" in web:
        # Misma imagen que construye web; no se descarga ni se construye otra vez.
        service.setdefault("image", f"{project}-{web_service}")
        service["pull_policy"] = "never"
    if "image" not in service:
        raise ValueError(f"El servicio {web_service} no define image ni build")
    networks = web.get("networks") or {}
    # Sin alias: el proxy resuelve web-ID y nunca debe llegar a un worker.
    service["networks"] = {net: {} for net in networks} if networks else {}
    depends = {k: v for k, v in (web.get("depends_on") or {}).items() if k not in (web_service, name)}
    if depends:
        service["depends_on"] = depends
    service.update(command=list(spec["command"]), restart="unless-stopped",
                   mem_limit=cfg.get(spec["memory"], "256m"), healthcheck={"disable": True})
    if not service["networks"]:
        del service["networks"]
    return service


def overlay(service, name):
    return json.dumps({"services": {name: service}}, indent=2) + "\n"


def write_overlay(path, content):
    path.parent.mkdir(mode=0o755, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("x") as stream:
        stream.write(content)
    temp.chmod(0o600)
    os.replace(temp, path)


def resolved(row):
    """Configuración resuelta sin interpolar: no contiene valores de runtime.env."""
    result = subprocess.run(compose_args(row) + ["config", "--format", "json", "--no-interpolate"],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def enable(row, name, service="web", memory=None, config=None):
    if row["kind"] != "laravel":
        raise ValueError("Los workers de Laravel solo se agregan a sitios de tipo laravel")
    path = overlay_path(row, name)
    if path.exists():
        raise ValueError(f"Ya existe {path.relative_to(ROOT)}")
    config = config if config is not None else resolved(row)
    services = config.get("services", {})
    if name in services:
        raise ValueError(f"El Compose ya define un servicio {name}; no se duplica")
    if service not in services:
        raise ValueError(f"El Compose no define el servicio {service}")
    cfg = dict(settings())
    if memory:
        if not re.fullmatch(r"[1-9][0-9]*[kmg]", memory):
            raise ValueError("Memoria inválida; usar por ejemplo 256m o 1g")
        cfg[WORKERS[name]["memory"]] = memory
    worker = derive(services[service], config.get("name", "dockerws-" + row["id"]), name, cfg, service,
                    ROOT / "sites" / row["domain"])
    write_overlay(path, overlay(worker, name))
    return path


def disable(row, name):
    path = overlay_path(row, name)
    if not path.exists():
        raise ValueError(f"No existe {path.relative_to(ROOT)}")
    path.unlink()


# --- Traspaso desde /etc/cron.d -------------------------------------------------------------

def cron_jobs(text):
    jobs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*=.*", line):
            continue
        jobs.append(line)
    return jobs


def check_cron_file(path, domain):
    path = Path(path)
    if path.parent != CRON_DIR or path.is_symlink() or not path.is_file():
        raise ValueError(f"Se espera un archivo regular en {CRON_DIR}: {path}")
    jobs = cron_jobs(path.read_text())
    if not jobs:
        raise ValueError(f"{path} no contiene tareas")
    for job in jobs:
        words = shlex.split(job, comments=True)
        if "schedule:run" not in job or domain not in words:
            raise ValueError(f"{path} contiene tareas que no son el scheduler de {domain}; edítalo a mano: {job}")
    return path


def find_cron_files(domain):
    found = []
    if CRON_DIR.is_dir():
        for path in sorted(CRON_DIR.iterdir()):
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if any("schedule:run" in job and domain in shlex.split(job, comments=True) for job in cron_jobs(text)):
                found.append(path)
    return found


def wait_window(now=time.time, sleep=time.sleep):
    """Espera hasta un segundo dentro de CRON_WINDOW: el cron de este minuto ya se disparó
    y queda margen para arrancar el scheduler antes del minuto siguiente."""
    while True:
        second = now() % 60
        if CRON_WINDOW[0] <= second <= CRON_WINDOW[1]:
            return now()
        sleep((CRON_WINDOW[0] - second) % 60 + 0.2)


def handoff(row, cron_file, start, now=time.time, sleep=time.sleep, retired=None):
    """Retira el cron y arranca el scheduler en el mismo minuto, sin huecos ni duplicados."""
    path = check_cron_file(cron_file, row["domain"])
    retired = Path(retired or CRON_RETIRED)
    retired.mkdir(parents=True, mode=0o700, exist_ok=True)
    started = wait_window(now, sleep)
    # cron ignora nombres con punto: el renombrado atómico desactiva la tarea al instante.
    hidden = path.with_name("." + path.name + ".dockerws-retirado")
    os.rename(path, hidden)
    try:
        start()
    except BaseException:
        os.rename(hidden, path)
        print(f"No arrancó el scheduler; se restituyó {path}.", file=sys.stderr)
        raise
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = retired / f"{path.name}.{stamp}"
    shutil.move(hidden, destination)
    if int(now() // 60) != int(started // 60):
        print("AVISO: el scheduler arrancó después del cambio de minuto. Comprueba en los logs "
              "si se ejecutaron las tareas de ese minuto.", file=sys.stderr)
    print(f"Cron retirado a {destination}; el scheduler del contenedor ejecuta desde el próximo minuto.")
    return destination


# --- Estado ----------------------------------------------------------------------------------

def parse_ps(output):
    output = output.strip()
    if not output:
        return []
    if output.startswith("["):
        return json.loads(output)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def evaluate(expected, rows):
    """Devuelve [(servicio, estado, ok)] para los servicios esperados."""
    by_service = {r.get("Service"): r for r in rows}
    result = []
    for name in expected:
        row = by_service.get(name)
        if row is None:
            result.append((name, "sin contenedor", False))
            continue
        state, health = row.get("State", ""), row.get("Health", "")
        label = state + (f" ({health})" if health else "")
        result.append((name, label, state == "running" and health in ("", "healthy", "starting")))
    return result


def proxy_args():
    return ["docker", "compose", "--project-directory", str(ROOT / "proxy"),
            "-f", str(ROOT / "proxy/compose.generated.yaml")]


def status(rows, include_proxy=True):
    failed = False
    targets = [({"domain": "proxy"}, proxy_args())] if include_proxy else []
    targets += [(row, compose_args(row)) for row in rows]
    for row, args in targets:
        expected = subprocess.run(args + ["config", "--services"], capture_output=True, text=True,
                                  check=True).stdout.split()
        ps = subprocess.run(args + ["ps", "--all", "--format", "json"], capture_output=True, text=True, check=True).stdout
        for service, label, ok in evaluate(expected, parse_ps(ps)):
            failed |= not ok
            print(f"{'OK   ' if ok else 'FALLA'} {row['domain']} {service}: {label}")
    return int(failed)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = p.add_subparsers(dest="action", required=True)
    e = commands.add_parser("enable")
    e.add_argument("domain")
    e.add_argument("name", choices=sorted(WORKERS))
    e.add_argument("--service", default="web")
    e.add_argument("--memory")
    e.add_argument("--cron-file")
    d = commands.add_parser("disable")
    d.add_argument("domain")
    d.add_argument("name", choices=sorted(WORKERS))
    h = commands.add_parser("cron-handoff")
    h.add_argument("domain")
    h.add_argument("cron_file")
    s = commands.add_parser("status")
    s.add_argument("domain", nargs="?")
    args = p.parse_args()
    if args.action == "status":
        from registry import sites
        return status([site(args.domain)] if args.domain else sites(), include_proxy=not args.domain)
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    os.umask(0o077)
    row = site(args.domain)
    if args.action == "enable":
        if args.cron_file:
            check_cron_file(args.cron_file, row["domain"])
        else:
            pending = find_cron_files(row["domain"])
            if pending:
                raise ValueError("Hay un cron del host para este scheduler: " + ", ".join(map(str, pending))
                                 + ". Indica --cron-file para retirarlo sin duplicar ejecuciones.")
        print(f"Creado {enable(row, args.name, args.service, args.memory).relative_to(ROOT)}")
    elif args.action == "disable":
        disable(row, args.name)
        print(f"Eliminado el overlay de {args.name}.")
    else:
        handoff(row, args.cron_file, lambda: subprocess.run(compose_args(row) + ["start", "scheduler"], check=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: docker compose terminó con código {exc.returncode}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, StopIteration, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc or 'sitio no registrado'}", file=sys.stderr)
        sys.exit(1)
