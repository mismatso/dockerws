#!/usr/bin/env python3
"""Fase 8: db-import en modalidades host y container."""
import argparse
import gzip
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import SiteTestCase
import backups


def ps(services):
    return subprocess.CompletedProcess([], 0, stdout="\n".join(services), stderr="")


class ImportTests(SiteTestCase):
    def args(self, file, confirm=True, domain="example.test"):
        return argparse.Namespace(domain=domain, file=str(file), confirm_overwrite=confirm)

    def dump(self, text, name="dump.sql", compress=False):
        path = self.root / name
        data = text.encode()
        path.write_bytes(gzip.compress(data) if compress else data)
        return path

    def capture_load(self, row):
        """Sustituye el cliente del motor por un proceso que guarda su entrada estándar."""
        output = self.root / "received.sql"
        command = [sys.executable, "-c", f"import sys; open({str(output)!r}, 'wb').write(sys.stdin.buffer.read())"]
        return output, patch.object(backups, "restore_command", return_value=command)

    def test_plain_and_gzip_load_identically_in_host_and_container(self):
        text = "CREATE TABLE t (id int);\nINSERT INTO t VALUES (1);\n"
        for i, mode in enumerate(("container", "host")):
            with self.subTest(mode=mode):
                domain = f"site{i}.test"
                row = self.create(domain, engine="mysql", mode=mode)
                if mode == "host":
                    (self.site_dir(domain) / "host-db.ready").touch()
                for compress in (False, True):
                    output, client = self.capture_load(row)
                    with client, patch.object(backups.subprocess, "run", return_value=ps(["db"])), patch("builtins.print"):
                        backups.import_sql(self.args(self.dump(text, f"d{i}{compress}.sql.gz", compress), domain=domain))
                    self.assertEqual(output.read_text(), text)

    def test_postgres_sets_role_and_requires_empty_database(self):
        row = self.create(engine="postgres")
        output, client = self.capture_load(row)
        with client, patch.object(backups.subprocess, "run", return_value=ps([])), \
                patch.object(backups, "query", return_value="0"), patch("builtins.print"):
            backups.import_sql(self.args(self.dump("CREATE TABLE t (id int);\n")))
        self.assertTrue(output.read_text().startswith(f"SET ROLE {row['database']['user']};\n"))
        with client, patch.object(backups.subprocess, "run", return_value=ps([])), \
                patch.object(backups, "query", return_value="3"), self.assertRaises(ValueError):
            backups.import_sql(self.args(self.dump("CREATE TABLE t (id int);\n")))

    def test_rejects_statements_outside_the_site_database(self):
        mysql = self.create(engine="mariadb")
        for text in ("USE `otra`;\n", "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `x`;\n", "  drop database x;\n",
                     "GRANT ALL ON *.* TO 'x'@'%';\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                backups.scan_sql(mysql, self.dump("CREATE TABLE a (id int);\n" + text))
        backups.scan_sql(mysql, self.dump("INSERT INTO t VALUES ('USE x');\n-- USE y\n"))
        pg = self.create("pg.test", engine="postgres")
        for text in ("\\connect otra\n", "CREATE ROLE x;\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                backups.scan_sql(pg, self.dump(text))
        with self.assertRaises(ValueError):
            backups.scan_sql(mysql, self.dump("\n\n"))

    def test_guards(self):
        row = self.create(engine="mysql")
        dump = self.dump("CREATE TABLE t (id int);\n")
        with self.assertRaises(ValueError):
            backups.import_sql(self.args(dump, confirm=False))
        link = self.root / "link.sql"
        link.symlink_to(dump)
        with self.assertRaises(ValueError):
            backups.import_sql(self.args(link))
        with patch.object(backups.subprocess, "run", return_value=ps(["db", "web"])), self.assertRaises(ValueError):
            backups.import_sql(self.args(dump))
        corrupt = self.root / "corrupt.sql.gz"
        corrupt.write_bytes(gzip.compress(b"CREATE TABLE t (id int);\n")[:-6])
        with self.assertRaises(ValueError):
            backups.import_sql(self.args(corrupt))
        self.create("lite.test", engine="sqlite")
        with self.assertRaises(ValueError):
            backups.import_sql(self.args(dump, domain="lite.test"))
        host = self.create("host.test", engine="mysql", mode="host")
        with self.assertRaises(ValueError):
            backups.import_sql(self.args(dump, domain=host["domain"]))


if __name__ == "__main__":
    unittest.main()
