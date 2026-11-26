"""Paths and settings.

Three layers, later ones win:
  1. DEFAULTS below,
  2. environment variables (handy for Docker; see ENV below and .env.example),
  3. data/settings.json (changes made in the app's Family settings, and per-install choices).
Secrets (the OpenRouter key) come only from the environment or a file, never from settings.json.
"""
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CC_DATA", ROOT / "data"))
_default_sf = ROOT / "engine" / ("stockfish.exe" if os.name == "nt" else "stockfish")
STOCKFISH = Path(os.environ.get("CC_STOCKFISH") or (_default_sf if _default_sf.exists() else (shutil.which("stockfish") or "/usr/games/stockfish")))
# Home-server mode listens on loopback only (a website relay reaches it). Docker sets CC_HOST=0.0.0.0.
HOST = os.environ.get("CC_HOST", "127.0.0.1")
PORT = int(os.environ.get("CC_PORT", "8765"))
SERVE_WEB = os.environ.get("CC_SERVE_WEB", "0") == "1"      # serve web/ directly (standalone / Docker)
TRUST_PROXY = os.environ.get("CC_TRUST_PROXY", "0") == "1"  # take the client IP from X-Forwarded-For

DEFAULTS = {
    # Engine pool shared by the opponent and the coach
    "engines": 3,
    "engine_threads": 2,
    "engine_hash_mb": 64,
    "coach_time": 0.8,          # seconds of full-strength analysis per coach request
    "quality_time": 0.3,        # seconds per position for grading moves (drives the 1-5 move stars)
    # AI coaching. Providers are tried in order; "engine" (spoken templates) always works and is what
    # players without AI access get.
    #   claude_cli: Claude Code headless (`claude -p`) with the account holder's subscription login
    #   siwc:       Sign in with ChatGPT plan usage (one-off sign-in on the server; see DECISIONS.md)
    #   openrouter: OpenRouter API with your key (pay per use; capped by the limits below)
    "ai_providers": ["claude_cli", "siwc"],
    "ai_subscription_profiles": [],   # profile ids allowed to use AI ("all" or a list); see coach.ai_allowed
    "ai_per_profile_per_hour": 20,
    "ai_per_day": 150,
    "claude_model": "haiku",
    "claude_timeout": 25,
    "siwc_model": "",                 # filled on sign-in from /v1/models
    "openrouter_model": "anthropic/claude-haiku-5.5",
    "openrouter_url": "https://openrouter.ai/api/v1/chat/completions",
    "openrouter_timeout": 20,
    # Relay to a website mailbox (home-server mode; off until configured, see DEPLOY.md)
    "relay_url": "",
    "relay_key_file": str(DATA / "secrets" / "relay.key"),
    # Limits
    "login_fail_limit": 10,
    "login_lock_minutes": 15,
    "requests_per_minute": 120,
}

# environment variable -> (setting, type)
ENV = {
    "CC_AI_PROVIDERS": ("ai_providers", "list"),
    "CC_AI_PROFILES": ("ai_profiles", "profiles"),
    "CC_AI_PER_DAY": ("ai_per_day", int),
    "CC_AI_PER_PROFILE_PER_HOUR": ("ai_per_profile_per_hour", int),
    "OPENROUTER_MODEL": ("openrouter_model", str),
    "CC_ENGINES": ("engines", int),
    "CC_ENGINE_THREADS": ("engine_threads", int),
    "CC_RELAY_URL": ("relay_url", str),
}


def _env_layer():
    out = {}
    for var, (key, typ) in ENV.items():
        v = os.environ.get(var)
        if v in (None, ""):
            continue
        if typ == "list":
            out[key] = [x.strip() for x in v.split(",") if x.strip()]
        elif typ == "profiles":
            out[key] = "all" if v.strip().lower() == "all" else [int(x) for x in v.split(",") if x.strip()]
        else:
            out[key] = typ(v)
    return out


def load():
    DATA.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    cfg.update(_env_layer())
    f = DATA / "settings.json"
    if f.exists():
        cfg.update(json.loads(f.read_text(encoding="utf-8")))
    return cfg


def save(changes):
    DATA.mkdir(parents=True, exist_ok=True)
    f = DATA / "settings.json"
    cur = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    cur.update(changes)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    tmp.replace(f)


def secret(name: str) -> str:
    """A secret from the environment (NAME) or a file (NAME_FILE, e.g. a Docker secret)."""
    v = os.environ.get(name, "").strip()
    if not v and os.environ.get(name + "_FILE"):
        try:
            v = Path(os.environ[name + "_FILE"]).read_text(encoding="utf-8").strip()
        except OSError:
            v = ""
    return v
