#!/usr/bin/env python3
"""Fase 4: app.env separado de runtime.env."""
import json
import shutil
import subprocess
import unittest

from support import SiteTestCase
import envfiles
import registry
import workers


class EnvTests(SiteTestCase):
    def config(self, row, env_file=None):
        env_file = env_file or [{"path": str(self.site_dir(row["domain"]) / "runtime.env"), "required": True}]
        return {"name": "dockerws-" + row["id"], "services": {"web": {"image": "x", "env_file": env_file}}}

    def test_new_sites_load_app_env_after_runtime_env(self):
        row = self.create(kind="laravel", engine="mysql", mode="container")
        web = self.compose()["services"]["web"]
        self.assertEqual(web["env_file"], ["./runtime.env", {"path": "./app.env", "required": False}])
        app = self.site_dir() / "app.env"
        self.assertEqual(app.stat().st_mode & 0o777, 0o600)
        self.assertEqual(envfiles.keys(app.read_text()), [])
        self.assertIn("MAIL_MAILER", app.read_text())
        self.assertNotIn("APP_KEY=", app.read_text())
        static = self.create("www.test", kind="static")
        self.assertFalse((self.site_dir(static["domain"]) / "app.env").exists())
        if shutil.which("docker"):
            result = subprocess.run(registry.compose_args(row) + ["config", "--quiet"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_adopt_existing_site_with_overlay_and_scheduler(self):
        row = self.create(kind="laravel")
        (self.site_dir() / "app.env").unlink()  # Sitio anterior a esta versión.
        workers.enable(row, "scheduler", config=self.config(row))
        created, changed = envfiles.adopt(row, config=self.config(row))
        self.assertTrue(created)
        overlay = self.compose(name="compose.d/10-app-env.yaml")
        self.assertEqual(overlay, {"services": {"web": {"env_file": [envfiles.APP_ENV]}}})
        scheduler = self.compose(name="compose.d/50-scheduler.yaml")["services"]["scheduler"]
        self.assertEqual(scheduler["env_file"][-1], envfiles.APP_ENV)
        self.assertEqual(len(changed), 2)
        # Idempotente si web ya carga app.env.
        loaded = self.config(row, [{"path": "./runtime.env"}, {"path": str(self.site_dir() / "app.env")}])
        self.assertEqual(envfiles.adopt(row, config=loaded), (False, []))

    def test_move_keys_keeps_credentials_and_backs_up(self):
        row = self.create(kind="laravel", engine="mysql")
        runtime = self.site_dir() / "runtime.env"
        runtime.write_text(runtime.read_text() + "APP_NAME=\"Ejemplo\"\nMAIL_HOST=smtp.example.com\n# MAIL_PORT=25\n")
        original = runtime.read_text()
        backup = envfiles.move(row, ["APP_NAME", "MAIL_HOST"])
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("APP_NAME", envfiles.keys(runtime.read_text()))
        self.assertIn("DB_PASSWORD", envfiles.keys(runtime.read_text()))
        self.assertIn("# MAIL_PORT=25", runtime.read_text())
        self.assertEqual(envfiles.keys((self.site_dir() / "app.env").read_text()), ["APP_NAME", "MAIL_HOST"])
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o600)
        for bad in (["DB_PASSWORD"], ["APP_KEY"], ["NO_EXISTE"], ["APP_NAME"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                envfiles.move(row, bad)

    def test_overlap_reports_names_only(self):
        row = self.create(kind="laravel")
        (self.site_dir() / "app.env").write_text("APP_ENV=staging\nAPP_NAME=x\n")
        self.assertEqual(envfiles.overlap(row), ["APP_ENV"])
        (self.site_dir() / "app.env").write_text("esto no es una variable\n")
        with self.assertRaises(ValueError) as error:
            envfiles.overlap(row)
        self.assertNotIn("esto no es", str(error.exception))


if __name__ == "__main__":
    unittest.main()
