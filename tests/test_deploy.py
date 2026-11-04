#!/usr/bin/env python3
"""Fase 3: deploy.sh en la estructura actual (app/ es un directorio)."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SiteTestCase
import deploy


class Recorder:
    """Ejecuta git, rsync y ganchos; registra docker y el respaldo sin ejecutarlos."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, command, **kwargs):
        label = self.label(command)
        self.calls.append(label)
        if self.fail_on and self.fail_on in label:
            raise subprocess.CalledProcessError(1, command)
        if command[0] in ("git", "rsync") or "/hooks/" in command[0]:
            return subprocess.run(command, check=True, **kwargs)
        return subprocess.CompletedProcess(command, 0)

    @staticmethod
    def label(command):
        if command[0] == "docker":
            rest = command[command.index("compose") + 1:]
            while rest and rest[0] in ("--project-directory", "--env-file", "-f"):
                rest = rest[2:]
            return "docker " + " ".join(rest)
        if command[0].endswith("python3") or "backups.py" in " ".join(command):
            return "backup " + command[-1]
        return " ".join(command)


@unittest.skipUnless(shutil.which("rsync") and shutil.which("git"), "requiere rsync y git")
class DeployTests(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.recorder = Recorder()
        self.patches += [
            patch.object(deploy, "run", self.recorder),
            patch.object(deploy, "build_quiet"),
            patch.object(deploy, "fix_owner"),
            patch.object(deploy, "running_services", return_value=["web", "scheduler"]),
            patch.object(deploy, "app_services", return_value=["web", "scheduler"]),
            patch("builtins.print"),
            patch.object(deploy, "COMPOSER_CACHE", self.root / "cache"),
        ]
        for item in self.patches[-7:]:
            item.start()

    def source(self, name="release", **files):
        folder = self.root / name
        defaults = {"artisan": "<?php\n", "composer.json": "{}\n", "public/index.php": "<?php\n",
                    "storage/logs/dev.log": "no debe copiarse\n", ".env": "APP_KEY=dev\n"}
        for path, content in {**defaults, **files}.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_text(content)
        return folder

    def steps(self):
        return [c for c in self.recorder.calls if not c.startswith(("git", "rsync"))]

    def test_laravel_sequence_from_directory(self):
        row = self.create("app.example.com", "laravel", "mysql", "container")
        app = self.site_dir("app.example.com") / "app"
        (app / "storage/logs/laravel.log").write_text("producción\n")
        (app / "vendor").mkdir()
        (app / "vendor/autoload.php").write_text("<?php\n")
        (app / "obsoleto.php").write_text("<?php\n")
        deploy.deploy(row, source=str(self.source()))
        steps = self.steps()
        expected = ["exec -T --user 33:33 web php artisan down --retry=60", "backup app.example.com",
                    "--no-scripts", "run-script post-autoload-dump", "artisan migrate --force", "artisan optimize",
                    "up -d --no-build --force-recreate --wait web scheduler", "exec -T --user 33:33 web php artisan up"]
        positions = [next(i for i, s in enumerate(steps) if e in s) for e in expected]
        self.assertEqual(positions, sorted(positions), steps)
        self.assertLess(self.recorder.calls.index(next(c for c in self.recorder.calls if c.startswith("rsync"))),
                        self.recorder.calls.index(next(c for c in self.recorder.calls if "--no-scripts" in c)))
        # Datos, dependencias y secretos se conservan; lo borrado en origen desaparece.
        self.assertEqual((app / "storage/logs/laravel.log").read_text(), "producción\n")
        self.assertFalse((app / "storage/logs/dev.log").exists())
        self.assertFalse((app / ".env").exists())
        self.assertTrue((app / "vendor/autoload.php").exists())
        self.assertFalse((app / "obsoleto.php").exists())
        self.assertEqual((app / "public/index.php").stat().st_mode & 0o022, 0)
        self.assertEqual(os.readlink(app / "public/storage"), "../storage/app/public")

    def test_failure_after_maintenance_stops_and_keeps_site_down(self):
        row = self.create("app.example.com", "laravel", "mysql", "host")
        self.recorder.fail_on = "migrate"
        messages = []
        with patch("builtins.print", side_effect=lambda *a, **k: messages.append(" ".join(map(str, a)))), \
                self.assertRaises(subprocess.CalledProcessError):
            deploy.deploy(row, source=str(self.source()))
        self.assertFalse(any("force-recreate" in c or c.endswith("artisan up") for c in self.recorder.calls))
        self.assertTrue(any("quedó en mantenimiento" in m for m in messages))

    def test_validation_happens_before_maintenance(self):
        row = self.create("app.example.com", "laravel")
        empty = self.root / "vacio"
        empty.mkdir()
        not_laravel = self.root / "otro"
        (not_laravel / "x").mkdir(parents=True)
        link = self.root / "enlace"
        link.symlink_to(self.source())
        for source in (empty, not_laravel, link, self.site_dir("app.example.com") / "app", self.root / "no-existe"):
            with self.subTest(source=source), self.assertRaises(deploy.DeployError):
                deploy.deploy(row, source=str(source))
        self.assertEqual(self.recorder.calls, [])
        with self.assertRaises(deploy.DeployError):
            deploy.deploy(self.create("blog.test", "wordpress", "mysql", "container"))

    def test_options_and_php_sites(self):
        row = self.create("app.example.com", "laravel", "postgres", "container")
        deploy.deploy(row, source=str(self.source()), backup=False, migrate=False, composer=False)
        steps = " | ".join(self.steps())
        self.assertNotIn("backup", steps)
        self.assertNotIn("migrate", steps)
        self.assertNotIn("composer", steps)
        self.assertIn("optimize", steps)
        rsync = next(c for c in self.recorder.calls if c.startswith("rsync"))
        self.assertNotIn("/vendor/", rsync)  # Sin composer, vendor/ viene del origen.
        self.recorder.calls.clear()
        php = self.create("www.example.com", "php")
        deploy.deploy(php, source=str(self.source("web", **{"index.php": "<?php echo 1;\n"})))
        steps = self.steps()
        self.assertFalse(any("artisan" in c or c.startswith("backup") for c in steps), steps)
        self.assertTrue(any("force-recreate" in c for c in steps))

    def test_hooks_must_be_root_owned_and_not_writable_by_others(self):
        row = self.create("app.example.com", "laravel")
        hooks = self.site_dir("app.example.com") / "hooks"
        hooks.mkdir(mode=0o755)
        marker = self.root / "hook.out"
        script = hooks / "post-deploy"
        script.write_text(f"#!/bin/sh\necho \"$DOCKERWS_SITE\" > {marker}\n")
        script.chmod(0o775)
        with self.assertRaises(deploy.DeployError):
            deploy.deploy(row)
        script.chmod(0o755)
        deploy.deploy(row)
        self.assertEqual(marker.read_text().strip(), "app.example.com")
        script.unlink()
        script.symlink_to("/bin/true")
        with self.assertRaises(deploy.DeployError):
            deploy.deploy(row)

    def test_git_ref_checkout(self):
        row = self.create("app.example.com", "laravel")
        origin = self.root / "origin.git"
        work = self.root / "work"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q", "-b", "main", str(work))
        self.source("work", **{"storage/logs/dev.log": ""})
        (work / "storage/logs/dev.log").unlink()
        (work / ".env").unlink()
        git("add", ".", cwd=work)
        git("commit", "-qm", "uno", cwd=work)
        git("clone", "-q", "--bare", str(work), str(origin))
        app = self.site_dir("app.example.com") / "app"
        shutil.rmtree(app)
        git("clone", "-q", str(origin), str(app))
        (work / "nuevo.php").write_text("<?php\n")
        git("add", ".", cwd=work)
        git("commit", "-qm", "dos", cwd=work)
        git("push", "-q", str(origin), "main", cwd=work)
        deploy.deploy(row, ref="main")
        self.assertTrue((app / "nuevo.php").exists())
        self.assertEqual(git("rev-parse", "HEAD", cwd=app), git("rev-parse", "HEAD", cwd=work))
        (app / "artisan").write_text("modificado en producción\n")
        with self.assertRaises(deploy.DeployError):
            deploy.deploy(row, ref="main")
        with self.assertRaises(deploy.DeployError):
            deploy.Deployment(row, source="x", ref="main")

    def test_services_to_recreate(self):
        defined = ["base", "db", "scheduler", "web", "extra"]
        self.assertEqual(deploy.services_to_recreate(defined, ["db", "web", "extra"], "web"), ["scheduler", "web", "extra"])
        self.assertEqual(deploy.services_to_recreate(defined, [], "web"), ["scheduler", "web"])
        self.assertEqual(deploy.services_to_recreate(["db", "app"], [], "app"), ["app"])

    def test_concurrent_deploy_is_refused(self):
        import fcntl
        row = self.create("app.example.com", "laravel")
        with (self.site_dir("app.example.com") / ".deploy.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaises(deploy.DeployError):
                deploy.deploy(row)


if __name__ == "__main__":
    unittest.main()
