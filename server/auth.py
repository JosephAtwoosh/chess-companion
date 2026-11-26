"""Family password, remembered devices, admin password and rate limits.

- Passwords are stored as PBKDF2-SHA256 (200k rounds) in the meta table, never in clear.
- A device that enters the family password gets a random 256-bit token (kept in the browser's
  localStorage). Only its SHA-256 is stored, so a copy of the database cannot log in.
  Devices can be listed and revoked by the admin.
- First-time setup (choosing both passwords) is only allowed from the server itself
  (or done from CC_FAMILY_PASSWORD / CC_ADMIN_PASSWORD at first start, for Docker).
"""
import hashlib
import hmac
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


def new_device(name: str) -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    store.run("INSERT INTO devices(token_hash,name,created,last_seen) VALUES(?,?,?,?)",
              (tok_hash(tok), (name or "device")[:60], now, now))
    return tok


def device(tok: str | None):
    if not tok or len(tok) > 100:
        return None
    d = store.one("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (tok_hash(tok),))
    if d and time.time() - d["last_seen"] > 300:
        store.run("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), d["id"]))
    return d


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
