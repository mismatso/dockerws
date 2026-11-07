#!/usr/bin/env python3
"""Reglas de acceso de images/php-apache/apache.conf: ocultos negados, /.well-known/ hacia la aplicación."""
import re
import unittest
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "images/php-apache/apache.conf"


def section(name):
    """Expresión de la sección <NAME "regex"> que niega el acceso."""
    match = re.search(rf'<{name} "([^"]+)">\s*Require all denied', CONF.read_text())
    if not match:
        raise AssertionError(f"apache.conf no tiene <{name}> con Require all denied")
    return re.compile(match.group(1))  # Las expresiones usadas valen igual en PCRE y en re.


class ApacheConfTests(unittest.TestCase):
    def test_files_match_denies_hidden_files_except_well_known(self):
        files = section("FilesMatch")
        for name in (".env", ".htaccess", ".htpasswd", ".git", ".well-known.bak", ".well-knownx",
                     "dump.sql", "copia.bak", "laravel.log", "php.ini", "composer.json", "composer.lock"):
            self.assertTrue(files.search(name), name)
        # Con una ruta inexistente, Apache evalúa el primer componente que falta: ".well-known".
        for name in (".well-known", "security.txt", "index.php", "assetlinks.json", "composer.phar"):
            self.assertFalse(files.search(name), name)

    def test_directory_match_denies_hidden_directories_except_well_known(self):
        directories = section("DirectoryMatch")
        for path in ("/var/www/html/public/.git", "/var/www/html/public/.git/objects",
                     "/var/www/html/.svn", "/var/www/html/public/.well-known/.oculto"):
            self.assertTrue(directories.search(path), path)
        for path in ("/var/www/html", "/var/www/html/public", "/var/www/html/public/.well-known",
                     "/var/www/html/public/.well-known/pki-validation", "/var/www/html/public/storage"):
            self.assertFalse(directories.search(path), path)


if __name__ == "__main__":
    unittest.main()
