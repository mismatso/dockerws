#!/usr/bin/env python3
"""Fase 0: overlays de Compose en compose.d/."""
import json
import shutil
import subprocess
import unittest

from support import SiteTestCase
import registry


class OverlayTests(SiteTestCase):
    def test_without_overlays_arguments_are_unchanged(self):
        row = self.create()
        args = registry.compose_args(row)
        self.assertEqual(args.count("-f"), 1)
        self.assertEqual(args[-1], str(self.site_dir() / "compose.yaml"))

    def test_overlays_load_after_compose_in_bytewise_order(self):
        row = self.create()
        overlays = self.site_dir() / "compose.d"
        overlays.mkdir()
        for name in ("50-b.yaml", "10-a.yaml", "Z.yaml", "ignored.json"):
            (overlays / name).write_text("{}\n")
        files = [a for i, a in enumerate(registry.compose_args(row)) if i and registry.compose_args(row)[i - 1] == "-f"]
        names = [f.rsplit("/", 1)[1] for f in files]
        self.assertEqual(names, ["compose.yaml", "10-a.yaml", "50-b.yaml", "Z.yaml"])
        # Bash y Python deben producir la misma lista.
        bash = self.bash('site_compose_files "$1"', "example.test").stdout.split()
        self.assertEqual(bash, files)

    def test_bash_without_compose_d(self):
        self.create()
        self.assertEqual(self.bash('site_compose_files "$1"', "example.test").stdout.split(),
                         [str(self.site_dir() / "compose.yaml")])

    @unittest.skipUnless(shutil.which("docker"), "requiere la CLI de Docker")
    def test_overlay_volume_replaces_same_target(self):
        row = self.create(kind="laravel")
        (self.site_dir() / "compose.d").mkdir()
        (self.site_dir() / "compose.d/20-test.yaml").write_text(json.dumps({"services": {"web": {"volumes": [
            {"type": "bind", "source": "./writable", "target": "/var/www/html/storage", "read_only": False}]}}}))
        result = subprocess.run(registry.compose_args(row) + ["config", "--format", "json", "--no-interpolate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        volumes = json.loads(result.stdout)["services"]["web"]["volumes"]
        storage = [v for v in volumes if v["target"] == "/var/www/html/storage"]
        self.assertEqual(len(storage), 1)
        self.assertTrue(storage[0]["source"].endswith("/writable"))


if __name__ == "__main__":
    unittest.main()
