#!/usr/bin/env python3
"""stack.sh site-rename: de los nombres antiguos (dockerws-sHASH, dwsHASH) a los derivados del dominio."""
import json
import os
import unittest
from unittest.mock import patch

from support import SiteTestCase
import rename
import scaffold

DOMAIN = "app.example.com"
OLD_ID, OLD_DB = "s0123456789ab", "dws0123456789ab"
OLD_PROJECT = "dockerws-" + OLD_ID
NEW_PROJECT, NEW_DB = "dws-app-example-com-2805", "dws_app_example_com_2805"
OLD_EDGE, NEW_EDGE = "dockerws-edge-" + OLD_ID, NEW_PROJECT + "-edge"


class Docker:
    """Simula docker: registra los comandos y responde a las consultas que hace rename.py."""

    def __init__(self, test):
        self.test, self.commands, self.queries = test, [], []
        self.fail_on = None
        self.primary = "bridge"

    def run(self, command, **kwargs):
        self.commands.append(command)
        if self.fail_on and self.fail_on in " ".join(command):
            raise rename.subprocess.CalledProcessError(1, command)
        if "backups.py" in " ".join(command):
            return rename.subprocess.CompletedProcess(command, 0, stdout=f"OK {DOMAIN}: /var/backups/dockerws/x\n")
        return rename.subprocess.CompletedProcess(command, 0, stdout="")

    def output(self, command):
        self.commands.append(command)
        text = " ".join(command)
        if "ps -aq" in text:
            return "c1\nc2\n"
        if "image ls" in text:
            return f"{OLD_PROJECT}-web:latest\n"
        if "network ls" in text:  # Red que Compose creó con su nombre por omisión.
            return text.split("project=")[1].split()[0] + "_default\n"
        if "NetworkSettings" in text:  # Red principal del proxy y redes conectadas.
            return f"{self.primary} {OLD_EDGE} {NEW_EDGE}\n"
        return ""

    def query(self, row, sql, database=None):
        self.queries.append(sql)
        if "information_schema.tables" in sql:
            return "4"
        return "0" if sql.startswith("SELECT") else ""


class RenameTests(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.legacy(DOMAIN, "laravel", "mysql", "host")
        self.docker = Docker(self)
        for item in [patch.object(rename, "run", self.docker.run), patch.object(rename, "output", self.docker.output),
                     patch.object(rename.database, "query", self.docker.query),
                     patch.object(rename, "project_name", side_effect=self.project),
                     patch.object(rename, "project_volumes", return_value={}),
                     patch.object(rename, "running", return_value=["web", "scheduler"]),
                     patch.object(rename, "cron_mentions", return_value=[]),
                     patch.object(rename, "network_owner", return_value=None),
                     patch.object(rename, "proxy_container", return_value="proxy1"),
                     patch.object(rename.deploy, "build_quiet"), patch.object(rename.backups, "scan_sql"),
                     patch.object(rename.backups, "load_sql"), patch("builtins.print")]:
            item.start()
            self.patches.append(item)

    def legacy(self, domain, kind, engine="none", mode=None, scheduler=True, old_id=OLD_ID, old_db=OLD_DB):
        """Un sitio como los que creaban las versiones anteriores, con un overlay del scheduler."""
        old_project = "dockerws-" + old_id
        row = self.create(domain, kind, engine, mode)
        path = self.site_dir(domain)
        ident, db = scaffold.derive_names(domain)
        for name in ("site.json", "compose.yaml", "runtime.env", "secrets/init.sql"):
            file = path / name
            if file.exists():
                mode = file.stat().st_mode & 0o777
                file.chmod(0o600)
                text = file.read_text().replace(scaffold.PROJECT_PREFIX + ident, old_project)
                file.write_text(text.replace(ident, old_id)
                                .replace(db.replace("_", "\\_"), old_db).replace(db, old_db))
                file.chmod(mode)
        # Antes de los nombres PROYECTO-CLAVE: sin «network» en site.json y redes y volúmenes sin nombre.
        data = json.loads((path / "site.json").read_text())
        data.pop("network")
        (path / "site.json").write_text(json.dumps(data, indent=2) + "\n")
        compose = json.loads((path / "compose.yaml").read_text())
        compose["networks"] = {"edge": {"external": True, "name": "dockerws-edge-" + old_id},
                               **{k: {"internal": True} for k in compose["networks"] if k == "database"}}
        if "volumes" in compose:
            compose["volumes"] = {"db-data": {}}
        (path / "compose.yaml").write_text(json.dumps(compose, indent=2) + "\n")
        if scheduler:
            (path / "compose.d").mkdir(exist_ok=True)
            (path / "compose.d/50-scheduler.yaml").write_text(json.dumps(
                {"services": {"scheduler": {"image": f"{old_project}-web", "pull_policy": "never"}}}, indent=2) + "\n")
        (path / "secrets/db-password").write_text("a" * 64 + "\n")
        return row

    def project(self, row):
        return json.loads((self.site_dir(row["domain"]) / "compose.yaml").read_text())["name"]

    def site(self, domain=DOMAIN):
        return rename.site(domain)

    def test_plan_detects_legacy_names(self):
        plan = rename.Plan(self.site())
        self.assertEqual((plan.old_project, plan.new_project), (OLD_PROJECT, NEW_PROJECT))
        self.assertEqual((plan.old_db, plan.new_db), (OLD_DB, NEW_DB))
        self.assertTrue(plan.rename_project and plan.rename_db)
        self.assertEqual(plan.host, "localhost")
        self.assertFalse(rename.Plan(self.site(), containers_only=True).rename_db)
        # Un sitio creado con la versión actual no tiene nada que renombrar.
        self.create("new.example.com", "php", "mysql", "container")
        self.assertTrue(rename.Plan(self.site("new.example.com")).empty)

    def test_plan_refusals(self):
        path = self.site_dir(DOMAIN)
        env = (path / "runtime.env").read_text()
        (path / "runtime.env").write_text(env + f"DB_URL=mysql://{OLD_DB}@localhost/{OLD_DB}\n")
        with self.assertRaisesRegex(ValueError, "DB_URL"):
            rename.Plan(self.site())
        self.assertFalse(rename.Plan(self.site(), containers_only=True).rename_db)
        (path / "runtime.env").write_text(env.replace(f"DB_USERNAME={OLD_DB}", "DB_USERNAME=otro"))
        with self.assertRaisesRegex(ValueError, "DB_USERNAME"):
            rename.Plan(self.site())
        (path / "runtime.env").write_text(env)
        with patch.object(rename, "project_name", return_value=OLD_PROJECT), \
                patch.object(rename, "project_volumes", return_value={"data": "x"}):
            compose = (path / "compose.yaml").read_text()
            (path / "compose.yaml").write_text(f"name: {OLD_PROJECT}\nservices: {{}}\n"
                                               f"networks: {{edge: {{external: true, name: {OLD_EDGE}}}}}\n")
            with self.assertRaisesRegex(ValueError, "JSON"):
                rename.Plan(self.site())
            (path / "compose.yaml").write_text(compose)
        with patch.object(rename, "project_name", return_value="appexamplecom"):
            plan = rename.Plan(self.site(), containers_only=True)
            self.assertFalse(plan.rename_project or plan.rename_db)
            self.assertEqual(plan.new_network, "appexamplecom-edge")
        # La red nueva existe y es de otro sitio; o compose.yaml no menciona la red vieja.
        with patch.object(rename, "network_owner", return_value="otro"), \
                self.assertRaisesRegex(ValueError, "no pertenece"):
            rename.Plan(self.site())
        compose = (path / "compose.yaml").read_text()
        (path / "compose.yaml").write_text(compose.replace(OLD_EDGE, "mi-red"))
        with self.assertRaisesRegex(ValueError, "no declara la red"):
            rename.Plan(self.site())

    def test_postgres_requires_containers_only(self):
        self.legacy("pg.example.com", "php", "postgres", "container", scheduler=False,
                    old_id="s1111111111ab", old_db="dws1111111111ab")
        row = self.site("pg.example.com")
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            rename.Plan(row)
        self.assertFalse(rename.Plan(row, containers_only=True).rename_db)

    def test_rewrite_compose_and_env(self):
        row = self.site()
        path = self.site_dir(DOMAIN)
        (path / "runtime.env").write_text(f"DB_DATABASE=\"{OLD_DB}\"\nDB_USERNAME={OLD_DB}\nOTRA=1\n")
        (path / "runtime.env").chmod(0o600)
        rename.rewrite_compose(row, OLD_PROJECT, NEW_PROJECT, {"db-data": f"{OLD_PROJECT}_db-data"})
        rename.rewrite_env(row, OLD_DB, NEW_DB)
        compose = self.compose(DOMAIN)
        self.assertEqual(compose["name"], NEW_PROJECT)
        self.assertEqual(compose["networks"]["edge"]["name"], "dockerws-edge-" + OLD_ID)  # El proxy no cambia.
        self.assertEqual(compose["volumes"]["db-data"], {"external": True, "name": f"{OLD_PROJECT}_db-data"})
        scheduler = json.loads((path / "compose.d/50-scheduler.yaml").read_text())
        self.assertEqual(scheduler["services"]["scheduler"]["image"], f"{NEW_PROJECT}-web")
        self.assertEqual((path / "runtime.env").read_text(), f"DB_DATABASE=\"{NEW_DB}\"\nDB_USERNAME={NEW_DB}\nOTRA=1\n")
        self.assertEqual((path / "runtime.env").stat().st_mode & 0o777, 0o600)

    def test_apply_renames_and_records(self):
        rename.Rename(rename.Plan(self.site())).execute()
        path = self.site_dir(DOMAIN)
        row = json.loads((path / "site.json").read_text())
        self.assertEqual((row["id"], row["database"]["name"], row["database"]["user"]), (OLD_ID, NEW_DB, NEW_DB))
        self.assertEqual(self.compose(DOMAIN)["name"], NEW_PROJECT)
        self.assertIn(f"DB_DATABASE={NEW_DB}\n", (path / "runtime.env").read_text())
        self.assertIn(r"dws\_app\_example\_com\_2805", (path / "secrets/init.sql").read_text())
        created = next(q for q in self.docker.queries if q.startswith("CREATE USER"))
        self.assertIn(f"'{NEW_DB}'@'localhost'", created)
        record = json.loads((path / rename.RECORD).read_text())
        self.assertEqual((record["old_project"], record["old_database"]), (OLD_PROJECT, OLD_DB))
        self.assertTrue((path / rename.SNAPSHOT).exists())
        # Orden: construir antes de quitar los contenedores viejos; arrancar al final.
        texts = [" ".join(c) for c in self.docker.commands]
        removed = next(i for i, t in enumerate(texts) if t.startswith("docker rm -f"))
        started = next(i for i, t in enumerate(texts) if "up -d --no-build --wait" in t)
        self.assertLess(removed, started)
        rename.deploy.build_quiet.assert_called_once()
        with self.assertRaisesRegex(ValueError, "drop-old"):
            with patch.object(rename.os, "geteuid", return_value=0), \
                    patch.object(rename.sys, "argv", ["rename.py", DOMAIN, "--apply"]):
                rename.main()

    def test_failure_restores_everything(self):
        path = self.site_dir(DOMAIN)
        before = {name: (path / name).read_bytes() for name in
                  ("site.json", "compose.yaml", "runtime.env", "secrets/init.sql", "compose.d/50-scheduler.yaml")}
        rename.backups.load_sql.side_effect = RuntimeError("fallo simulado")
        with self.assertRaises(RuntimeError):
            rename.Rename(rename.Plan(self.site())).execute()
        for name, data in before.items():
            self.assertEqual((path / name).read_bytes(), data, name)
        self.assertFalse((path / rename.RECORD).exists())
        self.assertFalse((path / rename.SNAPSHOT).exists())
        self.assertTrue(any(q.startswith("DROP DATABASE IF EXISTS `" + NEW_DB) for q in self.docker.queries))
        # El sitio vuelve a arrancar con el proyecto y la red viejos; la red nueva se quita.
        texts = [" ".join(c) for c in self.docker.commands]
        self.assertIn("up -d --no-build --wait --force-recreate", texts[-3])
        self.assertEqual(texts[-2:], [f"docker network disconnect {NEW_EDGE} proxy1", f"docker network rm {NEW_EDGE}"])
        # Si conectar el proxy falla, la vuelta atrás no intenta desconectarlo.
        self.docker.commands.clear()
        self.docker.fail_on = "network connect"
        with self.assertRaises(rename.subprocess.CalledProcessError):
            rename.Rename(rename.Plan(self.site())).execute()
        texts = [" ".join(c) for c in self.docker.commands]
        self.assertFalse(any("network disconnect" in t for t in texts))
        self.assertEqual(texts[-1], f"docker network rm {NEW_EDGE}")
        self.assertEqual((path / "compose.yaml").read_bytes(), before["compose.yaml"])

    def test_drop_old(self):
        with self.assertRaisesRegex(ValueError, "pendiente"):
            rename.drop_old(self.site())
        rename.Rename(rename.Plan(self.site())).execute()
        self.docker.queries.clear()
        with patch.object(rename, "project_containers", return_value=[]):
            rename.drop_old(self.site())
        self.assertEqual(self.docker.queries,
                         [f"DROP DATABASE IF EXISTS `{OLD_DB}`; DROP USER IF EXISTS '{OLD_DB}'@'localhost';"])
        self.assertIn(["docker", "image", "rm", f"{OLD_PROJECT}-web:latest"], self.docker.commands)
        self.assertFalse((self.site_dir(DOMAIN) / rename.RECORD).exists())

    def test_apply_moves_site_and_proxy_to_new_network(self):
        rename.Rename(rename.Plan(self.site())).execute()
        path = self.site_dir(DOMAIN)
        self.assertEqual(json.loads((path / "site.json").read_text())["network"], NEW_EDGE)
        networks = self.compose(DOMAIN)["networks"]
        self.assertEqual((networks["edge"]["name"], networks["default"]["name"]), (NEW_EDGE, NEW_PROJECT + "-default"))
        record = json.loads((path / rename.RECORD).read_text())
        self.assertEqual((record["old_network"], record["old_networks"]), (OLD_EDGE, [OLD_PROJECT + "_default"]))
        texts = [" ".join(c) for c in self.docker.commands]
        # Red nueva y proxy conectado antes del corte; contenedores recreados; compose del proxy regenerado.
        created = texts.index(f"docker network create --label org.dockerws.site={OLD_ID} {NEW_EDGE}")
        connected = texts.index(f"docker network connect {NEW_EDGE} proxy1")
        removed = next(i for i, t in enumerate(texts) if t.startswith("docker rm -f"))
        started = next(i for i, t in enumerate(texts) if "up -d --no-build --wait --force-recreate" in t)
        self.assertLess(created, connected)
        self.assertLess(connected, removed)
        self.assertLess(removed, started)
        self.assertTrue(texts[-1].endswith("scripts/render-compose.py"))

    def test_network_only_for_sites_with_readable_project(self):
        # Sitio creado con nombres legibles pero antes de dws-ID-edge: solo cambian las redes.
        row = self.create("new.example.com", "php")
        ident = row["id"]
        path = self.site_dir("new.example.com")
        (path / "site.json").write_text(json.dumps({k: v for k, v in row.items() if k != "network"}))
        compose = self.compose("new.example.com")
        compose["networks"] = {"edge": {"external": True, "name": "dockerws-edge-" + ident}}
        (path / "compose.yaml").write_text(json.dumps(compose))
        plan = rename.Plan(self.site("new.example.com"))
        self.assertFalse(plan.rename_project or plan.rename_db)
        self.assertEqual((plan.new_network, plan.internal), (f"dws-{ident}-edge", {"default": f"dws-{ident}-default"}))
        self.assertIn(f"Red frontal: dockerws-edge-{ident} → dws-{ident}-edge", plan.describe())
        rename.Rename(plan).execute()
        texts = [" ".join(c) for c in self.docker.commands]
        stop = next(i for i, t in enumerate(texts) if t.endswith(" stop"))
        self.assertIn("up -d --no-build --wait --force-recreate", texts[stop + 1])
        # Detenido: se recrea sin arrancar, para que no quede atado a la red vieja.
        row = self.site("new.example.com")
        (path / rename.RECORD).unlink()
        (path / "site.json").write_text(json.dumps({k: v for k, v in row.items() if k != "network"}))
        compose = self.compose("new.example.com")
        compose["networks"]["edge"]["name"] = "dockerws-edge-" + ident
        (path / "compose.yaml").write_text(json.dumps(compose))
        self.docker.commands.clear()
        with patch.object(rename, "running", return_value=[]):
            rename.Rename(rename.Plan(self.site("new.example.com"))).execute()
        self.assertTrue(any("up --no-start --no-build --force-recreate" in " ".join(c) for c in self.docker.commands))
        self.assertTrue(rename.Plan(self.site("new.example.com")).empty)

    def test_drop_old_removes_old_networks(self):
        rename.Rename(rename.Plan(self.site())).execute()
        self.docker.commands.clear()
        with patch.object(rename, "project_containers", return_value=[]), \
                patch.object(rename, "network_owner", return_value=""):
            rename.drop_old(self.site())
        texts = [" ".join(c) for c in self.docker.commands]
        self.assertIn(f"docker network disconnect {OLD_EDGE} proxy1", texts)
        removed = [t.split()[-1] for t in texts if t.startswith("docker network rm")]
        self.assertEqual(removed, [OLD_PROJECT + "_default", OLD_EDGE])
        self.assertNotIn(NEW_EDGE, removed)

    def test_drop_old_keeps_proxy_primary_network(self):
        # Si la red vieja es la principal del proxy, borrarla impediría que el proxy vuelva a arrancar.
        rename.Rename(rename.Plan(self.site())).execute()
        self.docker.commands.clear()
        self.docker.primary = OLD_EDGE
        with patch.object(rename, "project_containers", return_value=[]), \
                patch.object(rename, "network_owner", return_value=""), patch("builtins.print") as printed:
            rename.drop_old(self.site())
        texts = [" ".join(c) for c in self.docker.commands]
        self.assertIn(f"docker network disconnect {OLD_EDGE} proxy1", texts)
        self.assertNotIn(f"docker network rm {OLD_EDGE}", texts)
        self.assertIn("proxy-up --recreate", " ".join(str(c) for c in printed.call_args_list))

    def test_drop_old_refuses_if_site_still_uses_old_names(self):
        path = self.site_dir(DOMAIN)
        (path / rename.RECORD).write_text(json.dumps({"old_database": OLD_DB, "old_project": None, "host": "localhost"}))
        with self.assertRaisesRegex(ValueError, "base vieja"):
            rename.drop_old(self.site())
        self.assertEqual(self.docker.queries, [])
        (path / rename.RECORD).write_text(json.dumps({"old_network": OLD_EDGE}))
        with self.assertRaisesRegex(ValueError, "red vieja"):
            rename.drop_old(self.site())

    def test_stack_wrapper_requires_root(self):
        if os.geteuid() != 0:
            result = rename.subprocess.run(["bash", str(self.root / "scripts/stack.sh"), "site-rename", DOMAIN],
                                           capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("root", result.stderr)


if __name__ == "__main__":
    unittest.main()
