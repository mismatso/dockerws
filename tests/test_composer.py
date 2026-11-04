#!/usr/bin/env python3
"""Fase 1: composer en Laravel sin capabilities."""
import os
import unittest
from unittest.mock import patch

from support import SiteTestCase
import deploy


class ComposerTests(SiteTestCase):
    def steps(self, row, args=(), service="web"):
        return deploy.composer_plan(row, args, service)

    def test_laravel_install_runs_root_without_scripts_then_scripts_as_33(self):
        row = self.create(kind="laravel")
        steps = self.steps(row)
        self.assertEqual([k for k, _ in steps], ["cache", "run", "chown", "run"])
        root, scripts = steps[1][1], steps[3][1]
        self.assertIn("0:0", root)
        self.assertIn("--no-scripts", root)
        self.assertIn(f"{self.site_dir()}/app:/var/www/html", root)
        for arg in deploy.COMPOSER_DEFAULT:
            self.assertIn(arg, root)
        self.assertEqual(root[root.index("web") + 1], "composer")
        self.assertIn("33:33", scripts)
        self.assertEqual(scripts[-3:], ["run-script", "post-autoload-dump", "--no-interaction"])
        # El paso como 33 no monta app/ escribible.
        self.assertNotIn("-v", scripts)
        self.assertFalse(any("ALLOW_SUPERUSER" in a for a in scripts))

    def test_explicit_no_scripts_is_respected(self):
        row = self.create(kind="laravel")
        steps = self.steps(row, ["install", "--no-scripts"])
        self.assertEqual([k for k, _ in steps], ["cache", "run", "chown"])
        self.assertEqual(steps[1][1].count("--no-scripts"), 1)

    def test_custom_service_and_read_only_commands(self):
        row = self.create(kind="laravel")
        steps = self.steps(row, ["show", "--direct"], service="base")
        self.assertEqual(len(steps), 1)
        command = steps[0][1]
        self.assertIn("33:33", command)
        self.assertEqual(command[command.index("base") + 1:], ["composer", "show", "--direct"])

    def test_generic_php_keeps_scripts_and_rejects_unknown_or_static(self):
        row = self.create(kind="php")
        steps = self.steps(row)
        self.assertEqual([k for k, _ in steps], ["cache", "run"])
        self.assertNotIn("--no-scripts", steps[1][1])
        with self.assertRaises(ValueError):
            self.steps(row, ["exec", "phpunit"])
        with self.assertRaises(ValueError):
            self.steps(self.create("static.test", kind="static"))

    def test_fix_owner_refuses_symlink(self):
        row = self.create(kind="laravel")
        storage = self.site_dir() / "app/storage"
        os.rename(storage, self.site_dir() / "storage-real")
        storage.symlink_to(self.site_dir() / "storage-real")
        with self.assertRaises(ValueError):
            deploy.fix_owner(row)

    def test_fix_owner_chowns_tree_without_following_links(self):
        row = self.create(kind="laravel")
        log = self.site_dir() / "app/storage/logs/laravel.log"
        log.write_text("")
        (self.site_dir() / "app/storage/logs/link").symlink_to("/etc/passwd")
        calls = []
        with patch.object(deploy.os, "chown", side_effect=lambda p, u, g, follow_symlinks=True: calls.append((p, follow_symlinks))):
            deploy.fix_owner(row)
        self.assertTrue(any(p.endswith("laravel.log") for p, _ in calls))
        self.assertTrue(all(f is False for _, f in calls))

    def test_composer_runs_steps_in_order(self):
        row = self.create(kind="laravel")
        order = []
        with patch.object(deploy, "build_quiet"), \
                patch.object(deploy, "run", side_effect=lambda c: order.append("33" if "33:33" in c else "root")), \
                patch.object(deploy, "fix_owner", side_effect=lambda r: order.append("chown")), \
                patch.object(deploy, "COMPOSER_CACHE", self.root / "cache"):
            deploy.composer(row, [])
        self.assertEqual(order, ["root", "chown", "33"])
        # Con caché de configuración previa, se regenera al final.
        (self.site_dir() / "app/bootstrap/cache/config.php").write_text("<?php return [];\n")
        commands = []
        with patch.object(deploy, "build_quiet"), patch.object(deploy, "run", side_effect=commands.append), \
                patch.object(deploy, "fix_owner"), patch.object(deploy, "COMPOSER_CACHE", self.root / "cache"):
            deploy.composer(row, [])
        self.assertEqual(commands[-1][-3:], ["php", "artisan", "optimize"])
        self.assertEqual((self.root / "cache" / row["id"]).stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
