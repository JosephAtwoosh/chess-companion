"""SQLite storage: devices, bans, profiles, games, coach requests. One file: data/chess.db (WAL mode)."""
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS devices (
  id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE, name TEXT, created REAL, last_seen REAL,
  revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY, name TEXT, age_group TEXT, created REAL,
  rating REAL, games INTEGER DEFAULT 0, target REAL DEFAULT 0.65,
  prefs TEXT DEFAULT '{}', weaknesses TEXT DEFAULT '{}', deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY, profile_id INTEGER, color TEXT, started REAL, ended REAL,
  status TEXT,             -- playing | finished | abandoned
  result TEXT,             -- 1-0 / 0-1 / 1/2-1/2 / *
  score REAL,              -- player's score 1 / 0.5 / 0
  termination TEXT, level REAL, target REAL, rating_before REAL, rating_after REAL,
  moves TEXT DEFAULT '[]', takebacks INTEGER DEFAULT 0, coach_uses INTEGER DEFAULT 0,
  summary TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS games_profile ON games(profile_id, started);
CREATE TABLE IF NOT EXISTS coach_log (
  id INTEGER PRIMARY KEY, ts REAL, profile_id INTEGER, game_id INTEGER, kind TEXT,
  provider TEXT, ms INTEGER, ok INTEGER);
CREATE TABLE IF NOT EXISTS bans (
  id INTEGER PRIMARY KEY, ip TEXT UNIQUE, note TEXT, created REAL);
CREATE TABLE IF NOT EXISTS access_log (   -- sign-ins and blocked sign-in tries, kept 30 days
  id INTEGER PRIMARY KEY, ts REAL, ip TEXT, ua TEXT, event TEXT, device_id INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS access_ts ON access_log(ts);
"""

_local = threading.local()
_write = threading.Lock()


def db() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.DATA.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DATA / "chess.db", timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        for tbl, col, decl in (("profiles", "adapt", "TEXT DEFAULT '{}'"),       # columns added after v1
                               ("devices", "last_ip", "TEXT DEFAULT ''"), ("devices", "ua", "TEXT DEFAULT ''"),
                               ("devices", "banned", "INTEGER DEFAULT 0")):
            try:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass
        _local.conn = c
    return c


def q(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None


def run(sql, args=()):
    with _write:
        c = db()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def meta_get(k, default=None):
    r = one("SELECT v FROM meta WHERE k=?", (k,))
    return json.loads(r["v"]) if r else default


def meta_set(k, v):
    run("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def backup(keep=14) -> Path:
    """Consistent copy of the database into data/backups/, keeping the newest `keep`."""
    d = config.DATA / "backups"
    d.mkdir(parents=True, exist_ok=True)
    out = d / time.strftime("chess-%Y%m%d-%H%M%S.db")
    dst = sqlite3.connect(out)
    with _write:
        db().backup(dst)
    dst.close()
    for old in sorted(d.glob("chess-*.db"))[:-keep]:
        old.unlink()
    return out


def restore(src: Path):
    """Replace the database with a backup (server must be stopped)."""
    dst = config.DATA / "chess.db"
    for ext in ("-wal", "-shm"):
        p = Path(str(dst) + ext)
        if p.exists():
            p.unlink()
    shutil.copyfile(src, dst)
