#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import registry
import scaffold
import database
import backups
import provision_host_db


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("config", "templates", "images", "proxy", "scripts"):
            shutil.copytree(PROJECT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__", "compose.generated.yaml", "local.json"))
        (self.root / "sites").mkdir()
        self.patches = [patch.object(module, "ROOT", self.root) for module in (registry, scaffold, database, backups, provision_host_db)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def create(self, domain="example.test", kind="php", engine="none", mode=None):
        args = ["--domain", domain, "--type", kind, "--database", engine]
        if mode:
            args += ["--db-mode", mode]
        with patch("builtins.print"):
            scaffold.scaffold(scaffold.parser().parse_args(args))
        return registry.site(domain)

    def test_supported_matrix_valid_compose(self):
        combos = [("static", "none", "none")]
        for kind in ("php", "laravel"):
            combos += [(kind, "none", "none"), (kind, "sqlite", "file")]
            combos += [(kind, e, m) for e in ("mysql", "mariadb", "postgres") for m in ("container", "host")]
        combos += [("wordpress", e, m) for e in ("mysql", "mariadb") for m in ("container", "host")]
        for i, (kind, engine, mode) in enumerate(combos):
            with self.subTest(kind=kind, engine=engine, mode=mode):
                row = self.create(f"site{i}.test", kind, engine, mode)
                path = self.root / "sites" / row["domain"]
                cfg = json.loads((path / "compose.yaml").read_text())
                self.assertEqual("db" in cfg["services"], mode == "container")
                self.assertNotIn("ports", cfg["services"]["web"])
                self.assertNotIn("privileged", cfg["services"]["web"])
                if mode == "container":
                    self.assertNotIn("ports", cfg["services"]["db"])
                    self.assertTrue(cfg["networks"]["database"]["internal"])
                    self.assertNotIn("POSTGRES_USER", cfg["services"]["db"].get("environment", {}))
                if shutil.which("docker"):
                    result = subprocess.run(registry.compose_args(row) + ["config", "--quiet"], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(registry.sites()), 21)

    def test_refuses_duplicate_domain_alias_and_id_without_rotating(self):
        row = self.create(engine="mysql")
        secret = self.root / "sites/example.test/runtime.env"
        original = secret.read_bytes()
        with self.assertRaises(ValueError):
            self.create(engine="mysql")
        with self.assertRaises(ValueError):
            scaffold.scaffold(scaffold.parser().parse_args(["--domain", "other.test", "--type", "php", "--id", row["id"]]))
        with self.assertRaises(ValueError):
            scaffold.scaffold(scaffold.parser().parse_args(["--domain", "other.test", "--type", "php", "--alias", "example.test"]))
        self.assertEqual(secret.read_bytes(), original)
        self.assertFalse(list((self.root / "sites").glob(".new-*")))

    def test_rejects_invalid_combinations_and_injection(self):
        for domain, kind, engine, mode in [("../../escape", "php", "none", "none"),
                    ("example.test", "wordpress", "postgres", "container"),
                    ("example.test", "static", "mysql", "host"),
                    ("example.test", "php", "sqlite", "container")]:
            with self.subTest(domain=domain, kind=kind, engine=engine), self.assertRaises(ValueError):
                self.create(domain, kind, engine, mode)
        self.assertEqual(registry.sites(), [])

    def test_credentials_permissions_and_app_readability(self):
        row = self.create(engine="postgres")
        path = self.root / "sites" / row["domain"]
        self.assertEqual((path / "runtime.env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((path / "secrets").stat().st_mode & 0o777, 0o700)
        self.assertEqual((path / "app").stat().st_mode & 0o777, 0o755)
        sql = (path / "secrets/init.sql").read_text()
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE", sql)
        self.assertIn("REVOKE ALL ON DATABASE", sql)
        self.assertNotIn((path / "secrets/db-root-password").read_text().strip(), (path / "runtime.env").read_text())

    def test_host_socket_config_and_local_grants(self):
        row = self.create(engine="mariadb", mode="host")
        path = self.root / "sites" / row["domain"]
        cfg = json.loads((path / "compose.yaml").read_text())
        self.assertNotIn("db", cfg["services"])
        self.assertIn("DB_SOCKET=/run/host-db/mysqld.sock", (path / "runtime.env").read_text())
        sql = (path / "secrets/init.sql").read_text()
        self.assertIn("'@'localhost'", sql)
        self.assertNotIn("ON *.*", sql)
        self.assertNotIn("GRANT OPTION", sql)

    def test_client_commands_keep_password_out_of_argv(self):
        row = self.create(engine="mysql")
        command = database.mysql_command(row)
        self.assertIn("--defaults-extra-file=/run/secrets/db-client.cnf", command)
        self.assertFalse(any("--password" in p for p in command))
        with patch.object(database, "query", return_value="0"):
            self.assertIn("--single-transaction", database.dump_command(row))
        with patch.object(database, "query", return_value="1"), patch("builtins.print"):
            self.assertIn("--lock-all-tables", database.dump_command(row))

    def test_sqlite_backup_includes_wal_and_detects_corruption(self):
        row = self.create(engine="sqlite")
        source = self.root / "sites/example.test/data/sqlite/database.sqlite"
        with sqlite3.connect(source) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE example (id INTEGER)")
            db.execute("INSERT INTO example VALUES (42)")
            db.commit()
            output = self.root / "snapshot"
            output.mkdir()
            backups.backup_one(row, output)
        result = backups.verify(row, output)
        with sqlite3.connect(result) as db:
            self.assertEqual(db.execute("SELECT id FROM example").fetchone()[0], 42)
        result.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            backups.verify(row, output)

    def test_sql_dump_failure_or_empty_never_gets_manifest(self):
        row = self.create(engine="mysql")
        for code in ("print('partial'); raise SystemExit(2)", "pass"):
            with tempfile.TemporaryDirectory(dir=self.root) as directory:
                with patch.object(backups, "dump_command", return_value=[sys.executable, "-c", code]):
                    with self.assertRaises((RuntimeError, ValueError)):
                        backups.backup_one(row, Path(directory))
                self.assertFalse((Path(directory) / "manifest.json").exists())

    def test_retention_only_removes_valid_managed_backups(self):
        row = self.create(engine="sqlite")
        base = self.root / "backups"
        base.mkdir()
        old = base / "20200101T000000Z-12345678"
        old.mkdir()
        backups.backup_one(row, old)
        os.utime(old, (0, 0))
        manual = base / "manual"
        manual.mkdir()
        invalid = base / "20200101T000000Z-87654321"
        invalid.mkdir()
        os.utime(invalid, (0, 0))
        backups.prune(row, base, 30)
        self.assertFalse(old.exists())
        self.assertTrue(manual.exists())
        self.assertTrue(invalid.exists())

    def test_restore_rejects_wrong_site_and_running_web(self):
        row = self.create(engine="sqlite")
        other = self.create("other.test", engine="sqlite")
        output = self.root / "snapshot"
        output.mkdir()
        backups.backup_one(row, output)
        with self.assertRaises(ValueError):
            backups.verify(other, output)
        args = argparse.Namespace(domain=row["domain"], folder=str(output), confirm_overwrite=False)
        with self.assertRaises(ValueError):
            backups.restore(args)
        args.confirm_overwrite = True
        with patch.object(backups.subprocess, "run", return_value=argparse.Namespace(stdout="web\ndb\n")):
            with self.assertRaises(ValueError):
                backups.restore(args)

    def test_backup_directory_rejects_public_or_symlink(self):
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            backups.private_directory(public)
        link = self.root / "linked"
        link.symlink_to(public)
        with self.assertRaises(ValueError):
            backups.private_directory(link)

    def test_host_provision_refuses_existing_resources(self):
        row = self.create(engine="mysql", mode="host")
        args = argparse.Namespace(domain=row["domain"], admin_cnf=None)
        with patch.object(Path, "is_socket", return_value=True), patch.object(provision_host_db, "query", return_value="1") as query:
            with self.assertRaises(ValueError):
                provision_host_db.provision(args)
            self.assertEqual(query.call_count, 1)
        self.assertFalse((self.root / "sites/example.test/host-db.ready").exists())

    def test_host_provision_creates_local_account_once(self):
        row = self.create(engine="mariadb", mode="host")
        args = argparse.Namespace(domain=row["domain"], admin_cnf=None)
        with patch.object(Path, "is_socket", return_value=True), patch.object(provision_host_db, "query", side_effect=["0", ""]) as query, patch("builtins.print"):
            provision_host_db.provision(args)
            self.assertEqual(query.call_count, 2)
            self.assertIn("'@'localhost'", query.call_args.args[1])
            provision_host_db.provision(args)
            self.assertEqual(query.call_count, 2)

    def test_postgres_hba_priority_reload_and_rollback(self):
        row = self.create(engine="postgres", mode="host")
        hba = self.root / "pg_hba.conf"
        original = "local all all peer\n"
        hba.write_text(original)
        with patch.object(provision_host_db, "query", side_effect=[str(hba), "0", "t"]), patch.object(os, "fchown"):
            provision_host_db.postgres_hba(row)
        result = hba.read_text()
        self.assertTrue(result.startswith("# dockerws example.test\nlocal " + row["database"]["name"]))
        self.assertIn("scram-sha-256", result)
        self.assertIn("reject", result)
        self.assertTrue(result.endswith(original))
        (self.root / "sites/example.test/secrets/pg_hba.before").unlink()
        hba.write_text(original)
        with patch.object(provision_host_db, "query", side_effect=[str(hba), "1", "t"]), patch.object(os, "fchown"):
            with self.assertRaises(ValueError):
                provision_host_db.postgres_hba(row)
        self.assertEqual(hba.read_text(), original)

    def test_failed_backup_preserves_prior_backup(self):
        row = self.create(engine="sqlite")
        base = self.root / "backups"
        folder = base / row["domain"]
        folder.mkdir(parents=True, mode=0o700)
        old = folder / "20200101T000000Z-12345678"
        old.mkdir()
        backups.backup_one(row, old)
        os.utime(old, (0, 0))
        with patch.object(backups.subprocess, "Popen") as popen, patch("builtins.print"):
            popen.return_value.wait.return_value = 1
            self.assertEqual(backups.backup_all(argparse.Namespace(domain=row["domain"]), base), 1)
        self.assertTrue(old.exists())
        self.assertFalse(list(folder.glob(".pending-*")))

    def test_proxy_bootstrap_then_tls_and_preserves_manual(self):
        self.create()
        conf = self.root / "proxy/conf.d"
        conf.mkdir(exist_ok=True)
        (conf / "manual.conf").write_text("# manual\n")
        certs = self.root / "certs"
        env = dict(os.environ, LETSENCRYPT_DIR=str(certs))
        command = [sys.executable, str(self.root / "scripts/render-proxy.py")]
        subprocess.run(command, check=True, env=env)
        text = "".join(p.read_text() for p in conf.glob("*.conf"))
        self.assertIn("return 503", text)
        self.assertNotIn("ssl_certificate ", text)
        live = certs / "live/example.test"
        live.mkdir(parents=True)
        (live / "fullchain.pem").write_text("test")
        (live / "privkey.pem").write_text("test")
        subprocess.run(command, check=True, env=env)
        text = "".join(p.read_text() for p in conf.glob("*.conf"))
        self.assertIn("ssl_certificate ", text)
        self.assertIn("return 301 https://example.test", text)
        self.assertEqual((conf / "manual.conf").read_text(), "# manual\n")


if __name__ == "__main__":
    unittest.main()
