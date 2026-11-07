#!/usr/bin/env python3
"""Comprueba el desafío HTTP-01 sin Let's Encrypt: publica un token en el webroot y lo pide
para el dominio y cada alias, en el proxy local y por DNS público (como lo haría la CA)."""
import argparse
import http.client
import os
import secrets
import shutil
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from registry import site

WEBROOT = Path(os.environ.get("ACME_WEBROOT", "/var/lib/dockerws/acme"))
MAX_REDIRECTS = 10  # Igual que Let's Encrypt.


def fetch(url, connect_host=None, connect_port=None, timeout=10):
    """GET sin seguir redirecciones. connect_* permite fijar la IP sin cambiar el Host."""
    parts = urlsplit(url)
    host = connect_host or parts.hostname
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(host, connect_port or parts.port or 443, timeout=timeout,
                                                 context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(host, connect_port or parts.port or 80, timeout=timeout)
    try:
        path = parts.path + ("?" + parts.query if parts.query else "")
        connection.request("GET", path, headers={"Host": parts.netloc, "User-Agent": "dockerws-acme-check"})
        response = connection.getresponse()
        body = response.read(4096)
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        connection.close()


def resolve(name):
    return sorted({a[4][0] for a in socket.getaddrinfo(name, 80, proto=socket.IPPROTO_TCP)})


def follow(url, expected, fetcher=fetch):
    """Sigue redirecciones como la CA. Devuelve (ok, pasos, cloudflare)."""
    hops, cloudflare = [], False
    for _ in range(MAX_REDIRECTS + 1):
        try:
            status, headers, body = fetcher(url)
        except ssl.SSLError as exc:
            hops.append(f"{url} → TLS: {exc.reason or exc}")
            return False, hops, cloudflare
        except (OSError, http.client.HTTPException) as exc:
            hops.append(f"{url} → {type(exc).__name__}: {exc}")
            return False, hops, cloudflare
        cloudflare = cloudflare or "cf-ray" in headers or headers.get("server", "").lower() == "cloudflare"
        hops.append(f"{url} → {status}")
        if status in (301, 302, 303, 307, 308) and "location" in headers:
            url = urljoin(url, headers["location"])
            continue
        return status == 200 and body.strip() == expected, hops, cloudflare
    hops.append("demasiadas redirecciones")
    return False, hops, cloudflare


def hint(hops, cloudflare):
    text = " ".join(hops)
    if cloudflare and any(code in text for code in ("→ 525", "→ 526", "→ 521", "→ 522")):
        return ("Cloudflare no pudo conectar al origen por HTTPS. Antes del primer certificado, el origen no tiene "
                "TLS: desactiva el proxy (nube gris) para emitir, o excluye /.well-known/acme-challenge/ de "
                "«Always Use HTTPS» y de las reglas de redirección.")
    if "https://" in text and "→ 503" not in text and not cloudflare:
        return "Una redirección a HTTPS impide validar antes de tener certificado; revisa reglas externas."
    if "→ 404" in text:
        return "El token no llegó al webroot del proxy: ¿el DNS apunta a este servidor y se ejecutó proxy-up?"
    if cloudflare and "→ 403" in text:
        return "Cloudflare bloquea la petición (WAF, Bot Fight o desafío): excluye /.well-known/acme-challenge/."
    return ""


# El resolvedor responde que el nombre no existe (NXDOMAIN) o que no tiene direcciones.
NOT_FOUND = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}


def dns_hint(name, exc, which=None):
    """Un registro recién creado puede seguir «sin respuesta» por la caché negativa del host."""
    if exc.errno not in NOT_FOUND:
        return []
    lines = ["Si acabas de crear el registro, el resolvedor del host puede guardar la respuesta negativa durante "
             "el TTL negativo de la zona (el mínimo del SOA, a menudo de 5 minutos a 1 hora). "
             f"Compara con el DNS público: dig +short {name} @1.1.1.1"]
    if (which or shutil.which)("resolvectl"):
        lines.append("Si el DNS público ya responde, vacía la caché de systemd-resolved y repite --check: "
                     "resolvectl flush-caches")
    else:
        lines.append("Si el DNS público ya responde, espera a que venza ese TTL o vacía la caché del resolvedor local.")
    return lines


def check(names, webroot=WEBROOT, local=("127.0.0.1", 80), public=True, fetcher=fetch, out=print):
    token = secrets.token_urlsafe(32)
    expected = f"{token}.dockerws-check".encode()
    folder = webroot / ".well-known/acme-challenge"
    folder.mkdir(parents=True, exist_ok=True, mode=0o755)
    path = folder / token
    failed = False
    try:
        with path.open("xb") as stream:
            stream.write(expected)
        path.chmod(0o644)
        for name in names:
            url = f"http://{name}/.well-known/acme-challenge/{token}"
            try:
                status, _, body = fetcher(url, *local)
                ok, detail = status == 200 and body.strip() == expected, f"HTTP {status}"
            except (OSError, http.client.HTTPException) as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            failed |= not ok
            out(f"{'OK   ' if ok else 'FALLA'} local    {name}: {detail}")
            if not public:
                continue
            try:
                addresses = resolve(name)
            except socket.gaierror as exc:
                failed = True
                out(f"FALLA público  {name}: DNS sin respuesta ({exc.strerror})")
                for line in dns_hint(name, exc):
                    out(f"      {line}")
                continue
            ok, hops, cloudflare = follow(url, expected, fetcher)
            failed |= not ok
            via = " vía Cloudflare" if cloudflare else ""
            out(f"{'OK   ' if ok else 'FALLA'} público  {name} ({', '.join(addresses)}){via}: {' | '.join(hops)}")
            if not ok and hint(hops, cloudflare):
                out(f"      {hint(hops, cloudflare)}")
    finally:
        path.unlink(missing_ok=True)
    return int(failed)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("domain")
    p.add_argument("--local-only", action="store_true", help="solo comprobar el proxy local")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("Ejecutar como root")
    row = site(args.domain)
    return check([row["domain"], *row["aliases"]], public=not args.local_only)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, StopIteration) as exc:
        print(f"ERROR: {exc or 'sitio no registrado'}", file=sys.stderr)
        sys.exit(1)
