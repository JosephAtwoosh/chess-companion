<?php
// Local preview of /chess-companion/ under PHP's built-in server, for the home-server + website mode.
// Use it as the router, or include it from another router that owns the port:
//   php -S 0.0.0.0:80 tools/dev-router.php
// Mirrors the Apache .htaccess: data/ and .json/.md/.txt files are never served.
$uri = rawurldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?? '/');
if ($uri === '/chess-companion') { header('Location: /chess-companion/'); return true; }
$root = getenv('CC_WEB_ROOT') ?: dirname(__DIR__) . '/web';
if (str_contains($uri, '..') || preg_match('#^/chess-companion/data(/|$)|/\.[^/]*$|\.(md|json|txt|log|ini|py)$#', $uri)) {
    http_response_code(404); echo 'Not found'; return true;
}
$rel = substr($uri, strlen('/chess-companion'));
if ($rel === '/' || $rel === '') $rel = '/index.html';
$file = $root . $rel;
if (!is_file($file)) { http_response_code(404); echo 'Not found'; return true; }
if (str_ends_with($file, '.php')) { chdir(dirname($file)); $_SERVER['SCRIPT_NAME'] = $uri; $_SERVER['SCRIPT_FILENAME'] = $file; require $file; return true; }
$types = ['html' => 'text/html; charset=utf-8', 'js' => 'text/javascript', 'css' => 'text/css', 'svg' => 'image/svg+xml',
          'png' => 'image/png', 'webmanifest' => 'application/manifest+json'];
$ext = strtolower(pathinfo($file, PATHINFO_EXTENSION));
header('Content-Type: ' . ($types[$ext] ?? 'application/octet-stream'));
header('Cache-Control: no-cache');
readfile($file); return true;
