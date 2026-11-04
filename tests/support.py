"""Base compartida de las pruebas: copia el proyecto a un directorio temporal."""
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import registry  # noqa: E402
import scaffold  # noqa: E402

# Cada módulo enlaza su propio nombre ROOT al importarlo; se parchean todos.
MODULES = ("registry", "scaffold", "database", "backups", "provision_host_db",
           "workers", "deploy", "envfiles", "acme_check")


class SiteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("config", "templates", "images", "proxy", "scripts", "host"):
            shutil.copytree(PROJECT / name, self.root / name,
                            ignore=shutil.ignore_patterns("__pycache__", "compose.generated.yaml", "local.json"))
        (self.root / "sites").mkdir()
        self.patches = []
        for name in MODULES:
            try:
                module = importlib.import_module(name)
            except ModuleNotFoundError:
                continue
            if hasattr(module, "ROOT"):
                self.patches.append(patch.object(module, "ROOT", self.root))
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def create(self, domain="example.test", kind="php", engine="none", mode=None, extra=()):
        args = ["--domain", domain, "--type", kind, "--database", engine, *extra]
        if mode:
            args += ["--db-mode", mode]
        with patch("builtins.print"):
            scaffold.scaffold(scaffold.parser().parse_args(args))
        return registry.site(domain)

    def site_dir(self, domain="example.test"):
        return self.root / "sites" / domain

    def compose(self, domain="example.test", name="compose.yaml"):
        return json.loads((self.site_dir(domain) / name).read_text())

    def bash(self, script, *args, env=None, check=True):
        """Ejecuta Bash con lib.sh de la copia temporal."""
        command = ["bash", "-c", f"source {self.root}/scripts/lib.sh; {script}", "bash", *args]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if check and result.returncode:
            self.fail(result.stderr)
        return result
