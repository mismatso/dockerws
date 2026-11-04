#!/usr/bin/env python3
"""Fase 5: verificación local de HTTP-01 y correo opcional en issue-cert.sh."""
import functools
import http.server
import os
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SiteTestCase
import acme_check


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class AcmeTests(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.webroot = self.root / "acme"
        self.webroot.mkdir()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                 functools.partial(Handler, directory=str(self.webroot)))
        self.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def run_check(self, names, **kwargs):
        lines = []
        status = acme_check.check(names, self.webroot, ("127.0.0.1", self.port), out=lines.append, **kwargs)
        return status, lines

    def test_local_check_succeeds_and_removes_token(self):
        status, lines = self.run_check(["app.example.com", "www.example.com"], public=False)
        self.assertEqual(status, 0, lines)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("OK") for line in lines))
        self.assertEqual(list((self.webroot / ".well-known/acme-challenge").iterdir()), [])

    def test_local_failure_is_reported(self):
        lines = []
        status = acme_check.check(["app.example.com"], self.webroot, ("127.0.0.1", 9), public=False, out=lines.append)
        self.assertEqual(status, 1)
        self.assertTrue(lines[0].startswith("FALLA"))

    def test_public_check_follows_redirects_and_explains_cloudflare(self):
        def fake(url, *args):
            if url.startswith("http://"):
                return 301, {"location": url.replace("http://", "https://"), "server": "cloudflare", "cf-ray": "x"}, b""
            return 525, {"server": "cloudflare", "cf-ray": "x"}, b"error"
        ok, hops, cloudflare = acme_check.follow("http://app.example.com/.well-known/acme-challenge/t", b"t", fake)
        self.assertFalse(ok)
        self.assertTrue(cloudflare)
        self.assertEqual(len(hops), 2)
        self.assertIn("Cloudflare", acme_check.hint(hops, cloudflare))
        self.assertIn("proxy-up", acme_check.hint(["http://x → 404"], False))

    def test_public_check_with_dns_uses_same_token(self):
        local = functools.partial(acme_check.fetch, connect_host="127.0.0.1", connect_port=self.port)
        with patch.object(acme_check, "resolve", return_value=["192.0.2.10"]):
            lines = []
            status = acme_check.check(["app.example.com"], self.webroot, ("127.0.0.1", self.port),
                                      fetcher=lambda url, *a: local(url), out=lines.append)
        self.assertEqual(status, 0, lines)
        self.assertIn("192.0.2.10", lines[1])

    def test_issue_cert_arguments(self):
        """El correo es opcional y la forma anterior (dominio correo [--dry-run]) sigue válida."""
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        log = self.root / "certbot.log"
        (bin_dir / "certbot").write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
        (bin_dir / "certbot").chmod(0o755)
        self.create("app.example.com")
        script = self.root / "scripts/issue-cert.sh"
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

        def run(*args):
            return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=env)

        source = script.read_text()
        # Las comprobaciones de root y del deploy-hook dependen del host; se omiten en la copia de prueba.
        script.write_text(source.replace("require_root\n", "", 1)
                          .replace("[[ -x /etc/letsencrypt/renewal-hooks/deploy/50-dockerws ]]", "true"))
        cases = [(("app.example.com", "--dry-run"), "--register-unsafely-without-email"),
                 (("app.example.com", "admin@example.com", "--dry-run"), "--email admin@example.com"),
                 (("app.example.com", "--dry-run", "admin@example.com"), "--email admin@example.com")]
        for args, expected in cases:
            with self.subTest(args=args):
                log.unlink(missing_ok=True)
                result = run(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, log.read_text())
                self.assertIn("--dry-run", log.read_text())
        for args in (("app.example.com", "--otra"), ("app.example.com", "a@example.com", "b@example.com"),
                     ("app.example.com", "--check", "--dry-run")):
            with self.subTest(args=args):
                self.assertNotEqual(run(*args).returncode, 0)


if __name__ == "__main__":
    unittest.main()
