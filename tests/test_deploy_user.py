#!/usr/bin/env python3
"""Fase 7: despliegue con sudo limitado desde una carpeta de subida."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from support import SiteTestCase
import deploy


class DeployUserTests(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.root / "incoming"
        self.base.mkdir(mode=0o755)
        self.base.chmod(0o755)
        config = json.loads((self.root / "config/defaults.json").read_text())
        config["deploy_incoming_dir"] = str(self.base)
        (self.root / "config/local.json").write_text(json.dumps(config))
        self.row = self.create("app.example.com", "laravel")
        self.folder = self.base / "app.example.com"
        self.folder.mkdir(mode=0o750)
        self.me = os.getuid()

    def restrict(self, source=None, ref=None, backup=True, uid=None):
        return deploy.restrict_for_sudo(self.row, uid or self.me, source, ref, backup, root_uid=self.me)

    def test_sudo_user_deploys_only_from_own_incoming_folder(self):
        self.assertEqual(self.restrict(), str(self.folder))
        self.assertEqual(self.restrict(source=str(self.folder)), str(self.folder))
        self.assertIsNone(self.restrict(ref="main"))
        other = self.root / "otro"
        other.mkdir()
        with self.assertRaises(deploy.DeployError):
            self.restrict(source=str(other))
        with self.assertRaises(deploy.DeployError):
            self.restrict(source="/")

    def test_sudo_user_cannot_skip_backup(self):
        with self.assertRaises(deploy.DeployError):
            self.restrict(backup=False)

    def test_incoming_folder_ownership_and_links(self):
        with self.assertRaises(deploy.DeployError):
            self.restrict(uid=self.me + 12345)  # La carpeta no es del usuario que invoca.
        self.base.chmod(0o775)
        with self.assertRaises(deploy.DeployError):
            self.restrict()
        self.base.chmod(0o755)
        self.folder.rmdir()
        self.folder.symlink_to(self.root)
        with self.assertRaises(deploy.DeployError):
            self.restrict()
        self.folder.unlink()
        with self.assertRaises(deploy.DeployError):
            self.restrict()

    def test_sudo_uid_detection(self):
        with patch.dict(os.environ, {"SUDO_UID": "1001"}):
            self.assertEqual(deploy.sudo_uid(), 1001)
        with patch.dict(os.environ, {"SUDO_UID": "0"}):
            self.assertIsNone(deploy.sudo_uid())
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(deploy.sudo_uid())

    def test_installer_syntax_and_root_guard(self):
        script = self.root / "host/install-deploy-user.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        if os.geteuid() != 0:
            result = subprocess.run(["bash", str(script), "deploy", "app.example.com"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("root", result.stderr)


if __name__ == "__main__":
    unittest.main()
