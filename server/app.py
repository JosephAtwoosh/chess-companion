"""Chess Companion backend.  Run:  python run.py   (Docker: see Dockerfile)

Listens on 127.0.0.1 only. The browser reaches it through the website's api.php:
  - Docker / standalone (CC_SERVE_WEB=1): this server also serves the web app and answers api.php itself;
  - home server + website: on the server itself, the website's api.php forwards straight here;
  - on the live site, api.php is a mailbox and the relay worker below fetches requests from it
    over HTTPS and posts the answers back. Nothing on the home server is exposed to the internet.
"""
import concurrent.futures
import json
import logging
import logging.handlers
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess

from . import auth, coach, config, games, llm, rating, store

log = logging.getLogger("cc")


class ApiError(Exception):
    pass


# ------------------------------------------------------------------ operations
def _prof(pid):
    p = games.profile(int(pid))
    if not p:
        raise ApiError("No such player.")
    return p


def _game(gid):
    g = games.get(int(gid))
    if not g:
        raise ApiError("No such game.")
    return g


def profile_view(p):
    return {"id": p["id"], "name": p["name"], "age_group": p["age_group"], "games": p["games"],
            "target": p["target"], "level": round(rating.opponent_level(p["rating"], p["target"])),
            "prefs": json.loads(p["prefs"] or "{}")}


def stats(p):
    rows = store.q("SELECT id,started,score,level,target,result,termination,summary,coach_uses,takebacks,color "
                   "FROM games WHERE profile_id=? AND status='finished' ORDER BY id", (p["id"],))
    n = len(rows)
    last = rows[-20:]
    summ = [json.loads(r["summary"]) for r in rows]
    coach_n = store.one("SELECT COUNT(*) n FROM coach_log WHERE profile_id=?", (p["id"],))["n"]
    weak = json.loads(p["weaknesses"] or "{}")
    return {
        "games": n,
        "win_rate_all": round(sum(r["score"] for r in rows) / n, 3) if n else None,
        "win_rate_last20": round(sum(r["score"] for r in last) / len(last), 3) if last else None,
        "target": p["target"],
        "estimated_strength": round(p["rating"]),
        "opponent_level_now": round(rating.opponent_level(p["rating"], p["target"])),
        "level_history": [{"game": r["id"], "level": round(r["level"]), "score": r["score"]} for r in rows[-50:]],
        "accuracy_last10": [s.get("mean_accuracy") and round(s["mean_accuracy"], 1) for s in summ[-10:]],
        "unassisted_share_last10": [s.get("unassisted_share") and round(s["unassisted_share"], 2) for s in summ[-10:]],
        "coach_requests": coach_n,
        "habits": [{"habit": games.THEME_WORDS.get(k, k), "weight": v}
                   for k, v in sorted(weak.items(), key=lambda x: -x[1]) if v >= 0.5][:5],
    }


BLOCKED = "blocked"   # for this error the web app shows its "this device is blocked" screen


def handle(req: dict, token: str | None, ip: str, ua: str = "", via_relay: bool = False) -> dict:
    op = req.get("op")
    # Requests fetched from the live website are never "local", whatever address the hosting reports.
    local = auth.is_local(ip) and not via_relay
    if not auth.allow_request(token or ip):
        raise ApiError("Too many requests. Please wait a minute.")
    why = None if local else auth.blocked(token, ip)
    if op == "status":
        return {"setup": auth.is_setup(), "authed": bool(auth.device(token)) and not why, "local": local,
                "blocked": bool(why)}
    if why:
        if op == "login":
            d = auth.device_row(token)
            auth.log_access(ip, ua, "blocked", d and d["id"], f"{why} ban: {(req.get('device') or '')[:40]}")
        raise ApiError(BLOCKED)
    if op == "setup":
        if auth.is_setup():
            raise ApiError("Already set up.")
        if not local:
            raise ApiError("Setup is only possible on the server itself.")
        auth.set_password("family_pw", req.get("family", ""))
        auth.set_password("admin_pw", req.get("admin", ""))
        log.info("setup done")
        return {"token": auth.new_device(req.get("device", "server"))}
    if op == "login":
        if auth.login_locked(ip):
            raise ApiError("Too many wrong passwords. Try again in 15 minutes.")
        if not auth.check("family_pw", req.get("password", "")):
            auth.login_failed(ip)
            auth.log_access(ip, ua, "wrong_password", None, req.get("device") or "")
            log.warning("wrong family password from %s", ip)
            raise ApiError("That's not the family password.")
        log.info("new device %r from %s", req.get("device"), ip)
        tok = auth.new_device(req.get("device", ""), ip, ua)
        auth.log_access(ip, ua, "signed_in", auth.device_row(tok)["id"], req.get("device") or "")
        return {"token": tok}
    if op == "local_admin":
        # The admin page (admin.html). On the server itself no password is needed; anywhere else the admin password.
        if not local:
            if auth.login_locked("admin:" + ip):
                raise ApiError("Locked for 15 minutes.")
            if not auth.check("admin_pw", req.get("admin_password", "")):
                auth.login_failed("admin:" + ip)
                raise ApiError("Wrong admin password.")
        return bans_admin(req, ip, local)

    dev = auth.device(token, ip, ua)
    if not dev:
        raise ApiError("auth")

    if op == "profiles":
        return {"profiles": [profile_view(p) for p in store.q("SELECT * FROM profiles WHERE deleted=0 ORDER BY id")]}
    if op == "profile_create":
        name = (req.get("name") or "").strip()[:24]
        age = req.get("age_group") if req.get("age_group") in rating.START else "adult"
        if not name:
            raise ApiError("Please type a name (a first name or nickname is enough).")
        pid = store.run("INSERT INTO profiles(name,age_group,created,rating,target) VALUES(?,?,?,?,0.65)",
                        (name, age, time.time(), rating.START[age]))
        return {"profile": profile_view(_prof(pid))}
    if op == "profile_settings":
        p = _prof(req["pid"])
        if "target" in req:
            t = max(0.1, min(0.9, float(req["target"])))
            store.run("UPDATE profiles SET target=? WHERE id=?", (t, p["id"]))
            g = games.current(p["id"])
            if g and len(json.loads(g["moves"])) <= 2:   # a game that has barely started follows the new setting
                p2 = _prof(p["id"])
                store.run("UPDATE games SET target=?, level=? WHERE id=?", (t, rating.opponent_level(p2["rating"], t), g["id"]))
        if "prefs" in req and isinstance(req["prefs"], dict):
            prefs = json.loads(p["prefs"] or "{}")
            prefs.update({k: v for k, v in req["prefs"].items() if k in ("sound", "voice", "rate", "captions")})
            store.run("UPDATE profiles SET prefs=? WHERE id=?", (json.dumps(prefs), p["id"]))
        return {"profile": profile_view(_prof(p["id"]))}
    if op == "stats":
        return {"stats": stats(_prof(req["pid"]))}
    if op == "history":
        p = _prof(req["pid"])
        rows = store.q("SELECT id,started,ended,color,status,result,score,level,termination,moves FROM games "
                       "WHERE profile_id=? AND status!='abandoned' ORDER BY id DESC LIMIT 50", (p["id"],))
        for r in rows:
            r["moves"] = len(json.loads(r["moves"]))
            r["level"] = round(r["level"])
        return {"games": rows, "stats": stats(p)}
    if op == "game_current":
        g = games.current(_prof(req["pid"])["id"])
        return {"game": games.state(g) if g else None}
    if op == "game_new":
        g = games.new_game(_prof(req["pid"])["id"], req.get("color", "white"))
        return {"game": games.state(g)}
    if op == "game_open":
        return {"game": games.state(_game(req["gid"]))}
    if op == "move":
        g = games.move(_game(req["gid"])["id"], str(req.get("uci", ""))[:5])
        out = {"game": games.state(g)}
        fb = coach.move_feedback(g, _prof(g["profile_id"]))      # only after a hint
        if fb:
            out["feedback"] = fb
        return out
    if op == "undo":
        return {"game": games.state(games.undo(_game(req["gid"])["id"]))}
    if op == "resign":
        return {"game": games.state(games.resign(_game(req["gid"])["id"]))}
    if op == "pgn":
        return {"pgn": games.pgn(_game(req["gid"]))}
    if op == "coach":
        g = _game(req["gid"])
        p = _prof(g["profile_id"])
        kind = req.get("kind")
        level = 1
        if kind == "hint":
            s = json.loads(g["summary"])
            ply = str(len(json.loads(g["moves"])))
            level = s.setdefault("hints", {}).get(ply, 0) + 1
            s["hints"] = {ply: level}
            store.run("UPDATE games SET summary=? WHERE id=?", (json.dumps(s), g["id"]))
        return coach.answer(kind, games.get(g["id"]), p, level)
    if op == "admin":
        if auth.login_locked("admin:" + ip):
            raise ApiError("Locked for 15 minutes.")
        if not auth.check("admin_pw", req.get("admin_password", "")):
            auth.login_failed("admin:" + ip)
            raise ApiError("Wrong admin password.")
        return admin(req)
    raise ApiError("Unknown request.")


def admin(req):
    a = req.get("action")
    cfg = config.load()
    if a == "overview":
        return {"devices": store.q("SELECT id,name,created,last_seen,revoked,banned FROM devices ORDER BY id"),
                "profiles": [profile_view(p) for p in store.q("SELECT * FROM profiles WHERE deleted=0")],
                "ai": {"providers": cfg["ai_providers"], "subscription_profiles": coach.ai_profiles(cfg),
                       **llm.status()},
                "usage_24h": store.q("SELECT provider, COUNT(*) n, SUM(ok) ok FROM coach_log WHERE ts>? GROUP BY provider",
                                     (time.time() - 86400,))}
    if a == "revoke":
        store.run("UPDATE devices SET revoked=1 WHERE id=?", (int(req["device_id"]),))
        return {"done": True}
    if a == "ai_set":
        ch = {}
        if "subscription_profiles" in req:
            ch["ai_profiles"] = [int(x) for x in req["subscription_profiles"]]
        config.save(ch)
        return {"done": True}
    if a == "delete_profile":
        store.run("UPDATE profiles SET deleted=1 WHERE id=?", (int(req["pid"]),))
        return {"done": True}
    if a == "family_password":
        auth.set_password("family_pw", req.get("new", ""))
        return {"done": True}
    if a == "backup":
        return {"file": str(store.backup().name)}
    raise ApiError("Unknown admin action.")


def bans_admin(req, ip, local):
    a = req.get("action")
    if a == "overview":
        devs = store.q("SELECT id,name,created,last_seen,revoked,banned,last_ip,ua FROM devices ORDER BY last_seen DESC")
        for d in devs:
            d["ip_banned"] = bool(d["last_ip"] and auth.ip_ban(d["last_ip"]))
        rows = store.q("SELECT a.ts,a.ip,a.ua,a.event,a.device_id,a.detail,d.name device FROM access_log a "
                       "LEFT JOIN devices d ON d.id=a.device_id ORDER BY a.id DESC LIMIT 150")
        for r in rows:
            r["ip_banned"] = bool(auth.ip_ban(r["ip"]))
        return {"devices": devs, "bans": store.q("SELECT id,ip,note,created FROM bans ORDER BY created DESC"),
                "log": rows, "you": "this computer" if local else ip}
    if a in ("ban_device", "unban_device", "remove_device"):
        d = store.one("SELECT * FROM devices WHERE id=?", (int(req["device_id"]),))
        if not d:
            raise ApiError("No such device.")
        if a == "ban_device":
            if req.get("with_address") and d["last_ip"]:
                _ban_ip(d["last_ip"], f"device: {d['name']}", ip)
            store.run("UPDATE devices SET banned=1, revoked=1 WHERE id=?", (d["id"],))
            log.warning("device %s %r banned", d["id"], d["name"])
        elif a == "unban_device":   # it must type the family password again
            store.run("UPDATE devices SET banned=0 WHERE id=?", (d["id"],))
            log.warning("device %s %r unbanned", d["id"], d["name"])
        else:
            store.run("UPDATE devices SET revoked=1 WHERE id=?", (d["id"],))
        return {"done": True}
    if a == "ban_ip":
        return {"done": True, "ip": _ban_ip(req.get("ip", ""), req.get("note", ""), ip)}
    if a == "unban_ip":
        store.run("DELETE FROM bans WHERE id=?", (int(req["ban_id"]),))
        log.warning("ban %s lifted", req["ban_id"])
        return {"done": True}
    raise ApiError("Unknown admin action.")


def _ban_ip(text, note, admin_ip):
    try:
        b = auth.parse_ip(text)
    except ValueError:
        raise ApiError("That isn't an address. Use e.g. 81.2.3.4, or a range like 81.2.3.0/24.")
    if auth.would_cover_local(b):
        raise ApiError("The server's own address can't be banned.")
    if not auth.is_local(admin_ip) and auth.covers(b, admin_ip):
        raise ApiError("That would ban the address you are using now.")
    store.run("INSERT INTO bans(ip,note,created) VALUES(?,?,?) ON CONFLICT(ip) DO UPDATE SET note=excluded.note",
              (b, (note or "")[:80], time.time()))
    log.warning("address %s banned (%s)", b, note)
    return b


def respond(req, token, ip, ua="", via_relay=False):
    t = time.time()
    try:
        out = {"ok": True, **handle(req, token, ip, (ua or "")[:200], via_relay)}
    except (ApiError, ValueError, KeyError, TypeError) as e:
        msg = str(e) if not isinstance(e, (KeyError, TypeError)) else "Bad request."
        out = {"ok": False, "error": msg}
    except Exception:
        log.exception("error in %s", req.get("op"))
        out = {"ok": False, "error": "Something went wrong on the chess computer."}
    log.info("%s %s %s %.0fms", ip, req.get("op"), "ok" if out["ok"] else out["error"][:40], (time.time() - t) * 1000)
    return out


# ------------------------------------------------------------------ HTTP (loopback only)
WEB = (config.ROOT / "web").resolve()
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".png": "image/png", ".webmanifest": "application/manifest+json", ".json": "application/json",
        ".txt": "text/plain; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            return self._send(413, {"ok": False, "error": "Too big."})
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"ok": False, "error": "Bad request."})
        self._send(200, respond(req, self.headers.get("X-CC-Token"), self.client_ip(), self.user_agent()))

    def user_agent(self) -> str:
        peer = self.client_address[0].removeprefix("::ffff:")
        # api.php on this machine passes the browser's User-Agent on; a direct browser sends its own
        if auth.is_local(peer) and self.headers.get("X-CC-UA") is not None:
            return self.headers["X-CC-UA"]
        return self.headers.get("User-Agent") or ""

    def client_ip(self) -> str:
        peer = self.client_address[0]
        if peer.startswith("::ffff:"):
            peer = peer[7:]
        # Only the local PHP proxy (api.php on this machine) may tell us the real client address.
        if auth.is_local(peer) and self.headers.get("X-CC-Client-IP"):
            return self.headers["X-CC-Client-IP"]
        if config.TRUST_PROXY and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return peer

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True, "time": time.time()})
        if config.SERVE_WEB:
            return self._static(path)
        self._send(404, {"ok": False})

    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        f = (WEB / rel).resolve()
        if (WEB not in f.parents and f != WEB) or not f.is_file() or rel.startswith("data") or f.suffix in (".php", ".md"):
            return self._send(404, {"ok": False})
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


# ------------------------------------------------------------------ relay worker (live site)
def relay_worker(stop: threading.Event):
    cfg = config.load()
    url = cfg["relay_url"]
    try:
        with open(cfg["relay_key_file"], encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        log.error("relay key file missing: %s", cfg["relay_key_file"])
        return
    pool = concurrent.futures.ThreadPoolExecutor(6)
    backoff = 1

    reply_url = cfg.get("relay_reply_url") or url   # separate only in tests (PHP's dev server is single-threaded)

    def call(method, query, body=None, timeout=40):
        base = reply_url if query.startswith("worker=reply") else url
        r = urllib.request.Request(f"{base}?{query}", data=body, method=method,
                                   headers={"X-Relay-Key": key, "Content-Type": "application/json",
                                            "User-Agent": "chess-companion-relay"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.load(resp)

    def work(item):
        out = respond(item.get("body") or {}, item.get("token"), item.get("ip") or "0.0.0.0",
                      str(item.get("ua") or ""), via_relay=True)
        try:
            call("POST", f"worker=reply&id={item['id']}", json.dumps(out).encode(), timeout=20)
        except Exception as e:
            log.warning("relay reply failed: %s", e)

    log.info("relay worker polling %s", url)
    while not stop.is_set():
        try:
            items = call("GET", "worker=poll").get("requests", [])
            backoff = 1
            for it in items:
                pool.submit(work, it)
        except Exception as e:
            log.warning("relay poll failed (%s); retry in %ss", e, backoff)
            stop.wait(backoff)
            backoff = min(60, backoff * 2)


def backups(stop):
    while not stop.is_set():
        try:
            log.info("backup %s", store.backup().name)
            store.run("DELETE FROM access_log WHERE ts<?", (time.time() - 30 * 86400,))
        except Exception:
            log.exception("backup failed")
        stop.wait(86400)


def setup_logging():
    d = config.DATA / "logs"
    d.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(d / "server.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h, logging.StreamHandler()])


def seed_passwords():
    """First start without the setup screen (Docker): take the passwords from the environment."""
    fam, adm = config.secret("CC_FAMILY_PASSWORD"), config.secret("CC_ADMIN_PASSWORD")
    if not auth.is_setup() and fam and adm:
        auth.set_password("family_pw", fam)
        auth.set_password("admin_pw", adm)
        log.info("passwords set from the environment")


def main():
    setup_logging()
    store.db()
    seed_passwords()
    stop = threading.Event()
    threading.Thread(target=backups, args=(stop,), daemon=True).start()
    if config.load()["relay_url"]:
        threading.Thread(target=relay_worker, args=(stop,), daemon=True).start()
    # On Windows, address reuse would let a second copy share the port silently; refuse instead.
    ThreadingHTTPServer.allow_reuse_address = sys.platform != "win32"
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    cfg = config.load()
    log.info("Chess Companion on http://%s:%s (stockfish %s, web %s, AI %s%s)", config.HOST, config.PORT,
             config.STOCKFISH, "served" if config.SERVE_WEB else "via website",
             ",".join(cfg["ai_providers"]) or "off",
             "" if "openrouter" not in cfg["ai_providers"] else
             (" model " + cfg["openrouter_model"] + (" key set" if config.secret("OPENROUTER_API_KEY") else " KEY MISSING")))
    try:
        srv.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
