<?php
// Chess Companion: the website's only server-side file. It never checks passwords or stores games:
// it passes requests to the home server, which does all of that.
//
//  direct mode  (PHP running on the Windows home server, e.g. http://localhost/chess-companion/):
//               forwards each request to the backend on 127.0.0.1:8765.
//  mailbox mode (the live Linux hosting): the browser's request is written to data/queue/,
//               the home server's relay worker collects it over HTTPS (header X-Relay-Key, only its SHA-256 is
//               stored here in data/relay.json) and posts the answer back. The home server is never exposed.
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

const MAX_BODY = 65536;
$DATA = __DIR__ . '/data';
$QUEUE = $DATA . '/queue';

function out(int $code, array $obj): void { http_response_code($code); echo json_encode($obj); exit; }

$cfgFile = $DATA . '/relay.json';
$cfg = is_file($cfgFile) ? (json_decode((string)file_get_contents($cfgFile), true) ?: []) : [];
// A Windows home server always talks to its backend directly; Linux web hosting uses the mailbox.
$mode = getenv('CC_RELAY_MODE') ?: (PHP_OS_FAMILY === 'Windows' ? 'direct' : ($cfg['mode'] ?? 'mailbox'));
$ip = $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';

// ------------------------------------------------------------------ worker side (home-server relay)
if (isset($_GET['worker'])) {
    if ($mode !== 'mailbox') out(404, ['ok' => false]);
    // the hosting may terminate TLS at a proxy
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https';
    if (!$https && !getenv('CC_RELAY_ALLOW_HTTP')) out(403, ['ok' => false, 'error' => 'https only']);
    $lock = $DATA . '/relay-lock.json';
    $fails = is_file($lock) ? (json_decode((string)file_get_contents($lock), true) ?: []) : [];
    $fails = array_values(array_filter($fails, fn($t) => $t > time() - 900));
    if (count($fails) >= 10) out(429, ['ok' => false, 'error' => 'locked']);
    $key = $_SERVER['HTTP_X_RELAY_KEY'] ?? '';
    if (empty($cfg['worker_key_sha256']) || !hash_equals($cfg['worker_key_sha256'], hash('sha256', $key))) {
        $fails[] = time(); file_put_contents($lock, json_encode($fails)); out(403, ['ok' => false]);
    }
    if (!is_dir($QUEUE)) mkdir($QUEUE, 0700, true);
    @file_put_contents($DATA . '/worker-seen.txt', (string)time());
    if ($_GET['worker'] === 'poll') {
        $until = microtime(true) + 20;
        do {
            $items = [];
            foreach (glob($QUEUE . '/*.req') ?: [] as $f) {
                $work = substr($f, 0, -4) . '.work';
                if (!@rename($f, $work)) continue;               // claimed by another poll
                $item = json_decode((string)file_get_contents($work), true);
                if ($item && $item['ts'] > time() - 30) { $items[] = $item; } else { @unlink($work); }
                if (count($items) >= 8) break;
            }
            if ($items) out(200, ['ok' => true, 'requests' => $items]);
            usleep(80000);
            @file_put_contents($DATA . '/worker-seen.txt', (string)time());
        } while (microtime(true) < $until);
        out(200, ['ok' => true, 'requests' => []]);
    }
    if ($_GET['worker'] === 'reply') {
        $id = preg_replace('/[^a-f0-9]/', '', (string)($_GET['id'] ?? ''));
        if (strlen($id) !== 32 || !is_file("$QUEUE/$id.work")) out(404, ['ok' => false]);
        $body = (string)file_get_contents('php://input', false, null, 0, 1 << 20);
        file_put_contents("$QUEUE/$id.tmp", $body);
        rename("$QUEUE/$id.tmp", "$QUEUE/$id.resp");
        @unlink("$QUEUE/$id.work");
        out(200, ['ok' => true]);
    }
    out(400, ['ok' => false]);
}

// ------------------------------------------------------------------ browser side
if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(405, ['ok' => false, 'error' => 'POST only']);
$body = (string)file_get_contents('php://input', false, null, 0, MAX_BODY + 1);
if (strlen($body) > MAX_BODY || !is_array(json_decode($body, true))) out(400, ['ok' => false, 'error' => 'Bad request.']);
$token = substr((string)($_SERVER['HTTP_X_CC_TOKEN'] ?? ''), 0, 100);

if ($mode === 'direct') {
    $backend = $cfg['backend'] ?? 'http://127.0.0.1:8765/api';
    $ctx = stream_context_create(['http' => [
        'method' => 'POST', 'timeout' => 60, 'ignore_errors' => true,
        'header' => "Content-Type: application/json\r\nX-CC-Token: $token\r\nX-CC-Client-IP: $ip\r\n",
        'content' => $body]]);
    $resp = @file_get_contents($backend, false, $ctx);
    if ($resp === false) out(503, ['ok' => false, 'error' => 'The chess computer is not answering. Is it switched on?']);
    echo $resp; exit;
}

// mailbox
if (!is_dir($QUEUE)) mkdir($QUEUE, 0700, true);
$seen = (int)@file_get_contents($DATA . '/worker-seen.txt');
if ($seen < time() - 60) out(503, ['ok' => false, 'error' => 'The chess computer is not answering. Is it switched on?']);
// simple flood guard per IP: 90 requests a minute
$rl = $DATA . '/rate-' . hash('sha256', $ip) . '.txt';
$hits = array_filter(explode(',', (string)@file_get_contents($rl)), fn($t) => (int)$t > time() - 60);
if (count($hits) >= 90) out(429, ['ok' => false, 'error' => 'Too many requests. Please wait a minute.']);
$hits[] = time(); file_put_contents($rl, implode(',', $hits));
if (count(glob($QUEUE . '/*.req') ?: []) > 50) out(503, ['ok' => false, 'error' => 'Busy, please try again.']);

$id = bin2hex(random_bytes(16));
$item = ['id' => $id, 'ts' => time(), 'ip' => $ip, 'token' => $token, 'body' => json_decode($body, true)];
file_put_contents("$QUEUE/$id.tmp", json_encode($item));
rename("$QUEUE/$id.tmp", "$QUEUE/$id.req");
$until = microtime(true) + 45;
while (microtime(true) < $until) {
    if (is_file("$QUEUE/$id.resp")) {
        $resp = (string)file_get_contents("$QUEUE/$id.resp");
        @unlink("$QUEUE/$id.resp");
        echo $resp; exit;
    }
    usleep(50000);
}
@unlink("$QUEUE/$id.req"); @unlink("$QUEUE/$id.work");
out(504, ['ok' => false, 'error' => 'The chess computer took too long. Please try again.']);
