#!/usr/bin/env python3
"""Releases atómicos: estructura y adopción."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SiteTestCase
from test_deploy import Recorder
import deploy
import releases

DOMAIN = "app.example.com"
BACKUP = "/var/backups/dockerws/app.example.com/20261104T000000Z-abcd1234"


class ReleaseRecorder(Recorder):
    """Además registra adónde apunta app en cada paso y devuelve la ruta del respaldo."""

    def __init__(self, site, fail_on=None):
        super().__init__(fail_on)
        self.site = site
        self.targets = []

    def __call__(self, command, **kwargs):
        app = self.site / "app"
        self.targets.append(os.readlink(app) if app.is_symlink() else "dir")
        result = super().__call__(command, **kwargs)
        if self.calls[-1].startswith("backup"):
            return subprocess.CompletedProcess(command, 0, stdout=f"OK {DOMAIN}: {BACKUP}\n")
        return result

    def target_at(self, fragment):
        index = next(i for i, c in enumerate(self.calls) if fragment in c)
        return self.targets[index]


@unittest.skipUnless(shutil.which("rsync") and shutil.which("git"), "requiere rsync y git")
class ReleaseTests(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.row = self.create(DOMAIN, "laravel", "mysql", "container")
        self.site = self.site_dir(DOMAIN)
        self.populate(self.site / "app", "v1", ["2024_01_01_000000_create_users"])
        (self.site / "app/storage/logs/laravel.log").write_text("datos de producción\n")
        self.recorder = ReleaseRecorder(self.site)
        for item in [patch.object(deploy, "run", self.recorder), patch.object(deploy, "build_quiet"),
                     patch.object(deploy, "fix_owner"), patch("builtins.print"),
                     patch.object(deploy, "running_services", return_value=["db", "web", "scheduler"]),
                     patch.object(deploy, "app_services", return_value=["db", "web", "scheduler"]),
                     patch.object(deploy, "COMPOSER_CACHE", self.root / "cache")]:
            item.start()
            self.patches.append(item)

    def populate(self, folder, version, migrations):
        files = {"artisan": "<?php\n", "composer.json": "{}\n", "public/index.php": f"<?php // {version}\n",
                 ".env": "APP_KEY=desarrollo\n", "storage/logs/dev.log": "no se copia\n"}
        files.update({f"database/migrations/{m}.php": "<?php\n" for m in migrations})
        for name, content in files.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(content)
        return folder

    def source(self, version, migrations):
        folder = self.root / f"src-{version}"
        folder.mkdir()
        return self.populate(folder, version, migrations)

    def config(self):
        """Configuración resuelta equivalente a compose config (rutas absolutas)."""
        web = self.compose(DOMAIN)["services"]["web"]
        volumes = [dict(v, source=str(self.site / v["source"][2:])) for v in web["volumes"]]
        return {"services": {"web": dict(web, volumes=volumes)}}

    def init(self):
        with patch("workers.resolved", return_value=self.config()):
            deploy.init_releases(self.row)
        self.recorder.calls.clear()
        self.recorder.targets.clear()
        return releases.current(self.row)

    def deploy(self, version, migrations, **options):
        with patch.object(releases, "new_name", side_effect=lambda row, now=None: f"20261104T00000{version[-1]}Z"):
            deploy.deploy(self.row, source=str(self.source(version, migrations)), **options)
        return releases.current(self.row)

    # --- Estructura -------------------------------------------------------------------------

    def test_init_moves_app_and_storage_and_writes_overlay(self):
        workers_overlay = self.site / "compose.d/50-scheduler.yaml"
        workers_overlay.parent.mkdir()
        workers_overlay.write_text(json.dumps({"services": {"scheduler": {"volumes": [
            {"type": "bind", "source": "./app/storage", "target": "/var/www/html/storage", "read_only": False}]}}}))
        initial = self.init()
        app = self.site / "app"
        self.assertTrue(app.is_symlink())
        self.assertEqual(os.readlink(app), f"releases/{initial}")
        release = self.site / "releases" / initial
        self.assertEqual(list((release / "storage").iterdir()), [])
        self.assertEqual((self.site / "shared/storage/logs/laravel.log").read_text(), "datos de producción\n")
        overlay = json.loads((self.site / "compose.d/90-releases.yaml").read_text())
        self.assertEqual(list(overlay["services"]), ["web"])
        self.assertEqual(overlay["services"]["web"]["volumes"][0]["source"], "./shared/storage")
        scheduler = json.loads(workers_overlay.read_text())["services"]["scheduler"]
        self.assertEqual(scheduler["volumes"][0]["source"], "./shared/storage")
        self.assertEqual(releases.read_meta(self.row, initial)["migrations"], ["2024_01_01_000000_create_users"])
        self.assertIn("init", (self.site / "releases/history.log").read_text())
        # Revertir deja el sitio como estaba.
        deploy.revert_releases(self.row)
        self.assertFalse(app.is_symlink())
        self.assertEqual((app / "storage/logs/laravel.log").read_text(), "datos de producción\n")
        self.assertFalse((self.site / "compose.d/90-releases.yaml").exists())
        self.assertEqual(json.loads(workers_overlay.read_text())["services"]["scheduler"]["volumes"][0]["source"],
                         "./app/storage")
        self.assertFalse((app / releases.META).exists())

    def test_init_failure_restores_app(self):
        self.recorder.fail_on = "force-recreate"
        with patch("workers.resolved", return_value=self.config()), self.assertRaises(subprocess.CalledProcessError):
            deploy.init_releases(self.row)
        app = self.site / "app"
        self.assertFalse(app.is_symlink())
        self.assertEqual((app / "storage/logs/laravel.log").read_text(), "datos de producción\n")
        self.assertFalse((self.site / "compose.d/90-releases.yaml").exists())
        self.assertEqual(releases.names(self.row), [])

    def test_switch_is_atomic_and_refuses_directory(self):
        initial = self.init()
        (self.site / "releases/20990101T000000Z").mkdir()
        releases.switch(self.row, "20990101T000000Z")
        self.assertEqual(releases.current(self.row), "20990101T000000Z")
        self.assertFalse((self.site / ".app.tmp").exists())
        releases.switch(self.row, initial)
        for bad in ("../../etc", "otro", "20990101T000000Z/.."):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                releases.switch(self.row, bad)

    # --- Sitios sin releases ----------------------------------------------------------------

    def test_in_place_sites_reject_release_options(self):
        self.init()
        with self.assertRaises(deploy.DeployError):
            deploy.deploy(self.row)
        with patch.dict(os.environ, {"SUDO_UID": "1001", "SUDO_USER": "deploy"}):
            for options in ({"init": True}, {"revert": True}):
                with self.subTest(options=options), self.assertRaises(deploy.DeployError):
                    deploy.deploy(self.row, **options)
        with self.assertRaises(ValueError):
            deploy.composer_plan(self.row, ["install"])
        with patch.object(deploy, "apply") as apply:
            deploy.composer(self.row, ["show"])
        self.assertEqual(len(apply.call_args.args[1]), 1)



if __name__ == "__main__":
    unittest.main()
