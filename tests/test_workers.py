#!/usr/bin/env python3
"""Fase 2: scheduler como servicio y traspaso desde el cron del host."""
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from support import SiteTestCase
import registry
import workers


class Clock:
    def __init__(self, start):
        self.value = start
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


CRON = ("SHELL=/bin/bash\n# Scheduler\n"
        "* * * * * root /opt/dockerws/scripts/stack.sh site app.example.com exec -T --user 33:33 web "
        "php artisan schedule:run >/dev/null 2>&1\n")


class SchedulerTests(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.cron = self.root / "cron.d"
        self.cron.mkdir()
        self.patches += [patch.object(workers, "CRON_DIR", self.cron)]
        self.patches[-1].start()

    def scheduler(self, domain="app.example.com"):
        return self.compose(domain, "compose.d/50-scheduler.yaml")["services"]["scheduler"]

    def test_new_site_with_scheduler_inherits_web_without_alias_or_ports(self):
        self.create("app.example.com", "laravel", "mysql", "container", extra=["--scheduler"])
        web = self.compose("app.example.com")["services"]["web"]
        service = self.scheduler()
        self.assertEqual(service["command"], ["php", "artisan", "schedule:work"])
        self.assertEqual(service["user"], "33:33")
        self.assertEqual(service["restart"], "unless-stopped")
        self.assertEqual(service["mem_limit"], "256m")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["volumes"], web["volumes"])
        self.assertEqual(service["env_file"], web["env_file"])
        self.assertEqual(service["networks"], {"edge": {}, "database": {}})
        self.assertEqual(service["healthcheck"], {"disable": True})
        self.assertEqual(service["image"], "dws-app-example-com-2805-web")
        self.assertEqual(service["pull_policy"], "never")
        for key in ("build", "ports", "sysctls", "expose"):
            self.assertNotIn(key, service)
        # El código sigue de solo lectura; solo storage y bootstrap/cache son escribibles.
        writable = sorted(v["target"] for v in service["volumes"] if not v["read_only"])
        self.assertEqual(writable, ["/var/www/html/bootstrap/cache", "/var/www/html/storage"])
        self.assertEqual(service["depends_on"], {"db": {"condition": "service_healthy"}})

    def test_scheduler_rejected_for_other_kinds(self):
        with self.assertRaises(ValueError):
            self.create("www.example.com", "php", extra=["--scheduler"])

    @unittest.skipUnless(shutil.which("docker"), "requiere la CLI de Docker")
    def test_generated_and_resolved_overlays_are_valid_and_equivalent(self):
        row = self.create("app.example.com", "laravel", "postgres", "host", extra=["--scheduler"])
        result = subprocess.run(registry.compose_args(row) + ["config", "--quiet"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = self.scheduler()
        (self.site_dir("app.example.com") / "compose.d/50-scheduler.yaml").unlink()
        with patch("builtins.print"):
            workers.enable(row, "scheduler")
        resolved = self.scheduler()
        # Desde la configuración resuelta: rutas relativas y sin secretos interpolados.
        self.assertEqual([v["source"] for v in resolved["volumes"]], [v["source"] for v in generated["volumes"]])
        self.assertEqual(resolved["env_file"][0]["path"], "./runtime.env")
        self.assertNotIn("DB_PASSWORD", json.dumps(resolved))
        self.assertEqual(resolved["image"], generated["image"])

    def test_hand_edited_compose_with_base_service_and_custom_image(self):
        row = self.create("app.example.com", "laravel")
        config = {"name": "dockerws-" + row["id"], "services": {
            "base": {"build": {"context": "/x/image"}, "image": "example/app-base:local"},
            "web": {"image": "example/app-base:local", "user": "33:33", "read_only": True,
                    "env_file": [{"path": f"{self.site_dir('app.example.com')}/runtime.env", "required": True}],
                    "networks": {"edge": {"aliases": ["web-" + row["id"]]}}, "ports": ["8080:80"],
                    "volumes": [{"type": "bind", "source": f"{self.site_dir('app.example.com')}/app",
                                 "target": "/var/www/html", "read_only": True}],
                    "healthcheck": {"test": ["CMD", "true"]}}}}
        workers.enable(row, "scheduler", memory="128m", config=config)
        service = self.scheduler()
        self.assertEqual(service["image"], "example/app-base:local")
        self.assertNotIn("pull_policy", service)
        self.assertEqual(service["networks"], {"edge": {}})
        self.assertEqual(service["volumes"][0]["source"], "./app")
        self.assertEqual(service["mem_limit"], "128m")
        self.assertNotIn("ports", service)
        with self.assertRaises(ValueError):
            workers.enable(row, "scheduler", config=config)  # El overlay ya existe.

    def test_enable_refuses_existing_service_bad_memory_and_kind(self):
        row = self.create("app.example.com", "laravel")
        config = {"services": {"web": {"image": "x"}, "scheduler": {"image": "x"}}}
        with self.assertRaises(ValueError):
            workers.enable(row, "scheduler", config=config)
        with self.assertRaises(ValueError):
            workers.enable(row, "scheduler", memory="mucho", config={"services": {"web": {"image": "x"}}})
        php = self.create("www.example.com", "php")
        with self.assertRaises(ValueError):
            workers.enable(php, "scheduler", config=config)
        self.assertFalse(workers.overlay_path(row, "scheduler").exists())

    def test_cron_file_validation(self):
        path = self.cron / "app-example"
        path.write_text(CRON)
        self.assertEqual(workers.check_cron_file(path, "app.example.com"), path)
        self.assertEqual(workers.find_cron_files("app.example.com"), [path])
        self.assertEqual(workers.find_cron_files("other.example.com"), [])
        with self.assertRaises(ValueError):
            workers.check_cron_file(path, "other.example.com")
        path.write_text(CRON + "0 3 * * * root /usr/local/bin/otra-tarea\n")
        with self.assertRaises(ValueError):
            workers.check_cron_file(path, "app.example.com")
        with self.assertRaises(ValueError):
            workers.check_cron_file(self.root / "fuera", "app.example.com")

    def test_handoff_waits_for_window_and_retires_cron(self):
        row = self.create("app.example.com", "laravel")
        path = self.cron / "app-example"
        path.write_text(CRON)
        clock = Clock(1_800_000_000 + 50)  # Segundo 50: fuera de la ventana.
        seen = {}

        def start():
            seen["cron_present"] = path.exists()
            seen["second"] = clock.now() % 60
            clock.value += 2

        with patch("builtins.print"):
            destination = workers.handoff(row, path, start, clock.now, clock.sleep, self.root / "retirado")
        self.assertFalse(seen["cron_present"])
        self.assertTrue(workers.CRON_WINDOW[0] <= seen["second"] <= workers.CRON_WINDOW[1])
        self.assertFalse(path.exists())
        self.assertEqual(destination.read_text(), CRON)
        self.assertFalse(list(self.cron.iterdir()))

    def test_handoff_restores_cron_when_start_fails(self):
        row = self.create("app.example.com", "laravel")
        path = self.cron / "app-example"
        path.write_text(CRON)
        clock = Clock(1_800_000_000 + 10)

        def start():
            raise subprocess.CalledProcessError(1, "start")

        with patch("builtins.print"), self.assertRaises(subprocess.CalledProcessError):
            workers.handoff(row, path, start, clock.now, clock.sleep, self.root / "retirado")
        self.assertEqual(path.read_text(), CRON)
        self.assertEqual(clock.sleeps, [])

    def test_status_evaluation(self):
        rows = workers.parse_ps('{"Service":"web","State":"running","Health":"healthy"}\n'
                                '{"Service":"scheduler","State":"exited","Health":""}\n')
        result = workers.evaluate(["web", "scheduler", "db"], rows)
        self.assertEqual([ok for _, _, ok in result], [True, False, False])
        self.assertEqual(result[2][1], "sin contenedor")
        self.assertEqual(workers.parse_ps('[{"Service":"web","State":"running"}]')[0]["Service"], "web")
        self.assertEqual(workers.evaluate(["web"], [{"Service": "web", "State": "running", "Health": "unhealthy"}])[0][2], False)

    def test_status_ignores_services_without_replicas(self):
        config = {"services": {"base": {"image": "example/base", "deploy": {"replicas": 0}},
                               "assets": {"image": "example/base", "scale": 0},
                               "web": {"image": "example/base"},
                               "worker": {"image": "example/base", "deploy": {"replicas": 2}}}}
        self.assertEqual(workers.expected_services(config), (["web", "worker"], ["base", "assets"]))


if __name__ == "__main__":
    unittest.main()
