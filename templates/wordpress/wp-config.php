<?php
// Configuración de infraestructura; conservar al importar el código de WordPress.
define('DB_NAME', getenv('DB_DATABASE'));
define('DB_USER', getenv('DB_USERNAME'));
define('DB_PASSWORD', getenv('DB_PASSWORD'));
define('DB_HOST', getenv('DB_SOCKET') ? 'localhost:' . getenv('DB_SOCKET') : getenv('DB_HOST') . ':' . getenv('DB_PORT'));
define('DB_CHARSET', 'utf8mb4');
define('DB_COLLATE', '');
foreach (['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY', 'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT'] as $key) {
    define($key, getenv('WP_' . $key));
}
define('WP_HOME', getenv('WP_HOME'));
define('WP_SITEURL', getenv('WP_SITEURL'));
define('DISALLOW_FILE_EDIT', true);
define('DISALLOW_FILE_MODS', true);
define('FORCE_SSL_ADMIN', true);
define('WP_DEBUG', false);
if (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https') {
    $_SERVER['HTTPS'] = 'on';
}
$table_prefix = 'wp_';
if (!defined('ABSPATH')) {
    define('ABSPATH', __DIR__ . '/');
}
require_once ABSPATH . 'wp-settings.php';
