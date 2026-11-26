"""Language-model providers for the coach. All of them only *rephrase* engine facts.

  claude_cli  Claude Code in headless mode (`claude -p`), signed in with the account holder's
              Claude subscription on the server. No tools, no session saved. Proven working.
  siwc        Sign in with ChatGPT "plan usage" (Responses API billed to the ChatGPT Plus plan).
              Needs a one-off sign-in on the server:  python -m server.llm siwc-login
  openrouter  OpenRouter with your own API key (OPENROUTER_API_KEY). Pay per use; only used when
              listed in ai_providers, and capped by ai_per_day / ai_per_profile_per_hour.

Nothing ever falls back to a paid API on its own: OpenRouter is used only if you list it.
Secrets stay on the server (data/secrets/, or the environment); never in git, never sent to a website.
"""
import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser

from . import config

log = logging.getLogger("cc.llm")


class Unavailable(Exception):
    pass


# ------------------------------------------------------------------ Claude Code headless
def claude_cli(system: str, prompt: str, cfg) -> str:
    cwd = config.DATA / "claude-cwd"          # empty folder: no project files or CLAUDE.md
    cwd.mkdir(parents=True, exist_ok=True)
    exe = cfg.get("claude_exe") or "claude"
    cmd = [exe, "-p", "--model", cfg["claude_model"], "--tools", "", "--no-session-persistence",
           "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=cfg["claude_timeout"], cwd=cwd, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unavailable(f"claude: {e.__class__.__name__}")
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise Unavailable(f"claude exit {r.returncode}: {(r.stderr or out)[:200]}")
    low = out.lower()
    if "usage limit" in low or "rate limit" in low or "please run /login" in low or "not logged in" in low:
        raise Unavailable("claude: limit or login")
    return out


# ------------------------------------------------------------------ Sign in with ChatGPT
AUTH = "https://auth.openai.com/api/accounts/authorize"
TOKEN = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PORT = 1455
_siwc_lock = threading.Lock()


def _secret_file():
    d = config.DATA / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    return d / "siwc.json"


def _load_siwc():
    f = _secret_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _save_siwc(rec):
    f = _secret_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    tmp.replace(f)


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise Unavailable(f"siwc token {e.code}: {body}")


def siwc_login():
    """One-off, run on the server by the account holder:  python -m server.llm siwc-login"""
    rec = _load_siwc()
    host_id = rec.get("host_id") or f"urn:uuid:{uuid.uuid4()}"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    redirect = f"http://127.0.0.1:{PORT}/auth/callback"
    params = {"client_id": rec.get("client_id") or "dynamic_agent_client", "ext_agent_host_id": host_id,
              "response_type": "code", "redirect_uri": redirect, "scope": SCOPES, "resource": RESOURCE,
              "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge}
    if not rec.get("client_id"):
        params["agent_name_hint"] = "Chess Companion"
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/auth/callback":
                self.send_response(404); self.end_headers(); return
            got.update({k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Chess Companion: done. You can close this tab.</h2>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
    url = AUTH + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    print("Opening the ChatGPT sign-in page in your browser...")
    webbrowser.open(url)
    deadline = time.time() + 300
    srv.timeout = 2
    while not got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got:
        sys.exit("No answer from the sign-in page within 5 minutes.")
    if "error" in got:
        sys.exit(f"Sign-in stopped: {got.get('error')}")
    if got.get("state") != state:
        sys.exit("Sign-in answer did not match (state). Nothing saved.")
    client_id = got.get("client_id") or rec.get("client_id")
    if not client_id:
        sys.exit("Registration incomplete: no client id returned.")
    if rec.get("client_id") and got.get("client_id") and got["client_id"] != rec["client_id"]:
        sys.exit("A different client id came back; refusing it.")
    tok = _post_form(TOKEN, {"grant_type": "authorization_code", "client_id": client_id, "code": got["code"],
                             "code_verifier": verifier, "redirect_uri": redirect, "resource": RESOURCE})
    if "chatgpt.tokens.use.direct" not in tok.get("scope", ""):
        print("Signed in, but ChatGPT plan usage was not granted for this account.")
    rec = {"host_id": host_id, "client_id": client_id, "access_token": tok["access_token"],
           "refresh_token": tok.get("refresh_token"), "id_token": tok.get("id_token"),
           "scope": tok.get("scope", ""), "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60}
    _save_siwc(rec)
    m = _pick_model(rec["access_token"])
    if m:
        config.save({"siwc_model": m})
    print(f"Saved. Model: {m or '(none found)'}")
    print("Test:", siwc("Reply in five words.", "Say hello to a chess player.", config.load()))


def _access_token():
    with _siwc_lock:
        rec = _load_siwc()
        if not rec.get("access_token"):
            raise Unavailable("siwc: not signed in")
        if time.time() < rec["expires_at"]:
            return rec["access_token"]
        if not rec.get("refresh_token"):
            raise Unavailable("siwc: no refresh token")
        tok = _post_form(TOKEN, {"grant_type": "refresh_token", "client_id": rec["client_id"],
                                 "refresh_token": rec["refresh_token"], "resource": RESOURCE})
        rec.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token", rec["refresh_token"]),
                   scope=tok.get("scope", rec.get("scope")),
                   expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
        _save_siwc(rec)
        return rec["access_token"]


def _pick_model(token):
    req = urllib.request.Request(RESOURCE + "/models", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ids = [m["id"] for m in json.load(r).get("data", []) if m.get("visibility", "list") == "list"]
    except Exception as e:
        log.warning("siwc models: %s", e)
        return ""
    small = [i for i in ids if "mini" in i or "nano" in i]
    return (small or ids or [""])[0]


def siwc(system: str, prompt: str, cfg) -> str:
    if not cfg.get("siwc_model"):
        raise Unavailable("siwc: not set up")
    token = _access_token()
    body = json.dumps({"model": cfg["siwc_model"], "instructions": system,
                       "input": [{"role": "user", "content": prompt}], "store": False, "stream": True}).encode()
    req = urllib.request.Request(RESOURCE + "/responses", body,
                                 {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    text, done = [], False
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:]) if line[5:].strip() not in ("", "[DONE]") else {}
                t = ev.get("type")
                if t == "response.output_text.delta":
                    text.append(ev.get("delta", ""))
                elif t == "response.completed":
                    done = True
                elif t in ("response.failed", "error"):
                    raise Unavailable(f"siwc: {json.dumps(ev)[:200]}")
    except urllib.error.HTTPError as e:
        raise Unavailable(f"siwc {e.code}: {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        raise Unavailable(f"siwc: {e}")
    if not done:
        raise Unavailable("siwc: incomplete response")
    return "".join(text).strip()


# ------------------------------------------------------------------ OpenRouter (pay per use, your API key)
def openrouter(system: str, prompt: str, cfg) -> str:
    key = config.secret("OPENROUTER_API_KEY")
    if not key:
        raise Unavailable("openrouter: no OPENROUTER_API_KEY")
    body = json.dumps({"model": cfg["openrouter_model"], "max_tokens": 200, "temperature": 0.4,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(cfg["openrouter_url"], body, {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "X-Title": "Chess Companion"})
    try:
        with urllib.request.urlopen(req, timeout=cfg["openrouter_timeout"]) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # 401 bad key, 402 out of credits, 429 rate limited: never retried here, the coach falls back
        raise Unavailable(f"openrouter {e.code}: {e.read().decode(errors='replace')[:200]}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise Unavailable(f"openrouter: {e}")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise Unavailable(f"openrouter: unexpected reply {str(data)[:200]}")


PROVIDERS = {"claude_cli": claude_cli, "siwc": siwc, "openrouter": openrouter}
SUBSCRIPTION = {"claude_cli", "siwc"}


def status():
    rec = _load_siwc()
    return {"siwc_signed_in": bool(rec.get("refresh_token")), "siwc_model": config.load().get("siwc_model")}


if __name__ == "__main__":
    if sys.argv[1:] == ["siwc-login"]:
        siwc_login()
    elif sys.argv[1:2] == ["test"]:
        cfg = config.load()
        name = sys.argv[2]
        t = time.time()
        print(PROVIDERS[name]("Answer in one short sentence.", "Say hello to a young chess player.", cfg))
        print(f"{time.time() - t:.1f}s")
    else:
        print("usage: python -m server.llm siwc-login | test <claude_cli|siwc|openrouter>")
