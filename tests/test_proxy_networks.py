#!/usr/bin/env python3
"""Fase 6: proxy-up conecta redes nuevas sin recrear el proxy."""
import json
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


if __name__ == "__main__":
    unittest.main()
