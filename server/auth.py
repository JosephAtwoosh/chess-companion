"""Family password, remembered devices, admin password and rate limits.

- Passwords are stored as PBKDF2-SHA256 (200k rounds) in the meta table, never in clear.
- A device that enters the family password gets a random 256-bit token (kept in the browser's
  localStorage). Only its SHA-256 is stored, so a copy of the database cannot log in.
  Devices can be listed and revoked by the admin.
- Banning: a banned device is refused even if the family password is typed again on it (until its browser data is
  cleared), and a banned address (or range, e.g. 81.2.3.0/24) is refused for everything, sign-in included.
  The server itself (127.0.0.1) can never be banned, so the admin can't lock themselves out.
- First-time setup (choosing both passwords) is only allowed from the server itself
  (or done from CC_FAMILY_PASSWORD / CC_ADMIN_PASSWORD at first start, for Docker).
"""
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque

from . import config, store

ROUNDS = 200_000


def _hash_pw(pw: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ROUNDS)
    return {"salt": salt.hex(), "hash": h.hex(), "rounds": ROUNDS}


def _check_pw(pw: str, rec: dict | None) -> bool:
    if not rec:
        return False
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(rec["salt"]), rec["rounds"])
    return hmac.compare_digest(h.hex(), rec["hash"])


def is_setup() -> bool:
    return store.meta_get("family_pw") is not None


def set_password(kind: str, pw: str):
    assert kind in ("family_pw", "admin_pw")
    if len(pw) < 6:
        raise ValueError("Please use at least 6 characters.")
    store.meta_set(kind, _hash_pw(pw))


def check(kind: str, pw: str) -> bool:
    return _check_pw(pw, store.meta_get(kind))


def tok_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def new_device(name: str, ip: str = "", ua: str = "") -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    store.run("INSERT INTO devices(token_hash,name,created,last_seen,last_ip,ua) VALUES(?,?,?,?,?,?)",
              (tok_hash(tok), (name or "device")[:60], now, now, ip[:64], ua[:200]))
    return tok


def device_row(tok: str | None):
    """The device for a token, whether active, removed or banned."""
    if not tok or len(tok) > 100:
        return None
    return store.one("SELECT * FROM devices WHERE token_hash=?", (tok_hash(tok),))


def device(tok: str | None, ip: str = "", ua: str = ""):
    """The active device for a token (not removed, not banned); notes when and where it was last seen."""
    d = device_row(tok)
    if not d or d["revoked"] or d["banned"]:
        return None
    if time.time() - d["last_seen"] > 300 or (ip and ip != d["last_ip"]) or (ua and ua[:200] != d["ua"]):
        store.run("UPDATE devices SET last_seen=?, last_ip=COALESCE(NULLIF(?,''),last_ip), "
                  "ua=COALESCE(NULLIF(?,''),ua) WHERE id=?", (time.time(), ip[:64], ua[:200], d["id"]))
    return d


# ---- bans
def parse_ip(text: str) -> str:
    """'81.2.3.4' or a range '81.2.3.0/24', in canonical form; ValueError otherwise."""
    text = (text or "").strip()
    return str(ipaddress.ip_network(text, strict=False) if "/" in text else ipaddress.ip_address(text))


def covers(ban: str, ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a in ipaddress.ip_network(ban, strict=False) if "/" in ban else a == ipaddress.ip_address(ban)
    except ValueError:
        return False


def ip_ban(ip: str):
    if is_local(ip):
        return None
    for b in store.q("SELECT * FROM bans"):
        if covers(b["ip"], ip):
            return b
    return None


def would_cover_local(ban: str) -> bool:
    return any(covers(ban, x) for x in ("127.0.0.1", "::1"))


def blocked(tok: str | None, ip: str) -> str | None:
    """Why a request is refused ('device' or 'address'), or None."""
    if is_local(ip):
        return None
    d = device_row(tok)
    if d and d["banned"]:
        return "device"
    return "address" if ip_ban(ip) else None


def log_access(ip: str, ua: str, event: str, device_id=None, detail: str = ""):
    store.run("INSERT INTO access_log(ts,ip,ua,event,device_id,detail) VALUES(?,?,?,?,?,?)",
              (time.time(), ip[:64], (ua or "")[:200], event, device_id, (detail or "")[:60]))


# ---- rate limiting (in memory; resets on restart, which is fine) ----
_lock = threading.Lock()
_fails: dict[str, deque] = defaultdict(deque)
_reqs: dict[str, deque] = defaultdict(deque)


def login_locked(ip: str) -> bool:
    cfg = config.load()
    with _lock:
        d = _fails[ip]
        cutoff = time.time() - cfg["login_lock_minutes"] * 60
        while d and d[0] < cutoff:
            d.popleft()
        return len(d) >= cfg["login_fail_limit"]


def login_failed(ip: str):
    with _lock:
        _fails[ip].append(time.time())


def allow_request(key: str) -> bool:
    lim = config.load()["requests_per_minute"]
    now = time.time()
    with _lock:
        d = _reqs[key]
        while d and d[0] < now - 60:
            d.popleft()
        if len(d) >= lim:
            return False
        d.append(now)
        return True


def is_local(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost")
