#!/usr/bin/env python3
"""Fase 6: proxy-up conecta redes nuevas sin recrear el proxy."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import SiteTestCase
import proxy_networks
import workers


class ProxyNetworkTests(SiteTestCase):
    def test_plan_connects_new_and_disconnects_only_removed_sites(self):
        actions = proxy_networks.plan(
            ["dockerws-edge-a", "dockerws-edge-b"],
            ["dockerws-edge-a", "dockerws-edge-viejo", "bridge", "otra-red", ""])
        self.assertEqual(actions, [("connect", "dockerws-edge-b"), ("disconnect", "dockerws-edge-viejo")])
        self.assertEqual(proxy_networks.plan(["dockerws-edge-a"], ["dockerws-edge-a"]), [])

    def test_cli_reads_inventory(self):
        row = self.create("app.example.com", "laravel")
        result = subprocess.run([sys.executable, str(self.root / "scripts/proxy_networks.py")],
                                input="dockerws-proxy_default\n", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"connect dockerws-edge-{row['id']}\n")


    def test_generated_proxy_is_named_dws_proxy(self):
        row = self.create("app.example.com", "laravel")
        subprocess.run([sys.executable, str(self.root / "scripts/render-compose.py")], check=True)
        config = json.loads((self.root / "proxy/compose.generated.yaml").read_text())
        self.assertEqual(config["name"], "dws-proxy")
        self.assertEqual(config["services"]["proxy"]["container_name"], "dws-proxy")
        self.assertEqual(config["networks"]["edge-" + row["id"]]["name"], "dockerws-edge-" + row["id"])

    def test_status_accepts_legacy_proxy_until_replaced(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if "config" in command:
                out = json.dumps({"services": {"proxy": {}}})
            elif "-p" in command:  # Proyecto anterior: dockerws-proxy-proxy-1 sigue en marcha.
                out = '{"Service":"proxy","State":"running","Health":"healthy"}\n'
            else:
                out = ""
            return subprocess.CompletedProcess(command, 0, stdout=out)

        with patch.object(workers.subprocess, "run", fake_run), patch("builtins.print") as printed:
            self.assertEqual(workers.status([]), 0)
        self.assertIn(["docker", "compose", "-p", "dockerws-proxy", "ps", "--all", "--format", "json"], calls)
        printed.assert_any_call("OK    proxy proxy: running (healthy)")

    def test_lib_falls_back_to_legacy_proxy(self):
        script = self.root / "scripts/lib.sh"
        text = script.read_text()
        self.assertIn("LEGACY_PROXY_PROJECT=dockerws-proxy", text)
        # Sin docker: proxy_exec debe fallar con un mensaje claro, no ejecutar nada.
        result = self.bash("docker() { :; }; proxy_exec nginx -t", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("proxy-up", result.stderr)

    def test_site_network_is_created_before_first_deploy(self):
        row = self.create("app.example.com", "laravel")
        network = "dockerws-edge-" + row["id"]
        # docker simulado: la red no existe y se registra la creación.
        fake = 'docker() { echo "$*" >> "$LOG"; [[ "$1 $2" != "network inspect" ]]; }; site_network app.example.com'
        log = self.root / "docker.log"
        self.bash(fake, env={**os.environ, "LOG": str(log)})
        self.assertEqual(log.read_text().splitlines()[-1],
                         f"network create --label org.dockerws.site={row['id']} {network}")
        # Red con el nombre del sitio pero de otro dueño: no se usa.
        other = 'docker() { [[ $* == *-f* ]] && echo otro; return 0; }; site_network app.example.com'
        result = self.bash(other, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no pertenece a este sitio", result.stderr)
        # deploy.sh la asegura antes de respaldar o sincronizar; site-composer y env-reload, antes de Compose.
        deploy = (self.root / "scripts/deploy.sh").read_text()
        self.assertLess(deploy.index('site_network "$1"'), deploy.index("exec python3"))
        stack = (self.root / "scripts/stack.sh").read_text()
        for command in ("site-composer)", "env-reload)"):
            block = stack[stack.index(command):]
            self.assertLess(block.index("site_network"), block.index(";;"), command)


if __name__ == "__main__":
    unittest.main()
