#!/usr/bin/env python3
"""Fase 6: proxy-up conecta redes nuevas sin recrear el proxy."""
import subprocess
import sys
import unittest

from support import SiteTestCase
import proxy_networks


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


if __name__ == "__main__":
    unittest.main()
