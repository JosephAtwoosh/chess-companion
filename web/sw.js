// Offline shell only: static files are cached; api.php is never cached (games live on the server).
// tools/deploy.py replaces 'dev' below with a stamp of the files, so every upload gets a fresh cache.
const VERSION = 'dev';
const CACHE = 'cc-' + VERSION;
const SHELL = ['./', 'index.html', 'app.js?v=' + VERSION, 'style.css?v=' + VERSION, 'manifest.webmanifest', 'icon.svg',
  'vendor/chessground.min.js', 'vendor/chess.js', 'vendor/chessground.base.css', 'vendor/chessground.brown.css', 'vendor/chessground.cburnett.css'];
self.addEventListener('install', (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' })))).then(() => self.skipWaiting())));
self.addEventListener('activate', (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.endsWith('api.php')) return;
  // Network first so updates arrive at once; the saved copy is used only when offline.
  // cache: 'no-cache' makes the browser check with the website instead of reusing its own stored copy
  // (the web host tells browsers to keep .css for 30 days and .js for 2).
  e.respondWith(fetch(e.request.url, { cache: 'no-cache', credentials: 'same-origin' })
    .then((r) => { if (r.ok) { const c = r.clone(); caches.open(CACHE).then((x) => x.put(e.request, c)); } return r; })
    .catch(() => caches.match(e.request)));
});
