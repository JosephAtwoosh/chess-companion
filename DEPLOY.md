# Home server + website (no Docker)

In this mode a Windows PC at home runs the engine, the database and the coach. Your ordinary PHP web hosting serves
the page. The PC fetches the players' requests from the website over HTTPS and posts the answers back (a "mailbox"
relay), so no port is opened at home and nothing on the PC is reachable from the internet. The AI wording comes from
a subscription signed in on the PC (Claude Code, or Sign in with ChatGPT). For the simpler single-container setup, see
[Run with Docker](README.md#run-with-docker).

## 1. Install on the PC

```powershell
git clone https://github.com/JosephAtwoosh/chess-companion.git C:\chess-companion
pip install -r C:\chess-companion\requirements.txt
```

Download Stockfish from <https://github.com/official-stockfish/Stockfish/releases> (`stockfish-windows-x86-64-*.zip`)
and save the program as `C:\chess-companion\engine\stockfish.exe`.

## 2. Start it automatically

```powershell
powershell -ExecutionPolicy Bypass -File C:\chess-companion\tools\install-service.ps1
```

Run this as Administrator for "start at boot, no login needed". Without admin rights it registers "start at log on".
The scheduled task runs `tools/supervise.py`, which restarts the backend whenever it stops.

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Logs are in `data\logs\server.log` (requests, errors, AI provider problems) and `supervisor.log` (restarts).

## 3. First visit on the PC itself

Run PHP's built-in server with the project's router, then open <http://localhost/chess-companion/>:

```powershell
php -S 0.0.0.0:80 C:\chess-companion\tools\dev-router.php
```

The first visit asks for the family and admin passwords. This is only possible from the PC itself.

## 4. Put it on your website

1. Make the relay key (once). The key stays on the PC; the website gets only its SHA-256:

   ```powershell
   cd C:\chess-companion; python tools\relay_key.py https://your-site.example/chess-companion/api.php
   ```

2. Run `python tools\deploy.py C:\upload\chess-companion` to copy the web files to a staging folder. Copy
   `web-live\data\relay.json` into its `data\` folder, then upload the whole folder to `/chess-companion/` on your
   site, including `.htaccess`, `data/.htaccess` and `data/relay.json`. The hosting needs PHP 7.4 or newer and Apache
   `.htaccess` support (or equivalent rules that deny `data/`).
3. Restart the backend so it starts polling: `schtasks /end /tn "Chess Companion"`, then `schtasks /run /tn "Chess Companion"`.
4. `server.log` should stop showing `relay poll failed`. Open the page on a phone, type the family password, and play.

The website never checks passwords and never stores games or AI credentials. For up to about 45 seconds it holds the
requests waiting in `data/queue/`, which `.htaccess` blocks. After 10 wrong relay keys the worker endpoint locks for
15 minutes. Browsers are limited to 90 requests a minute per address.

To update the site later, run `deploy.py` again and upload the folder (`deploy.py` never touches anything in `data/`).

## 5. AI coaching (optional; engine coaching always works)

- **Claude:** if Claude Code is installed and signed in on the PC, tick the account holder's player in Family settings.
  The backend runs `claude -p` (headless, model haiku, no tools).
- **ChatGPT plan (Sign in with ChatGPT):** on the PC, as the account holder, run `python -m server.llm siwc-login`
  and approve in the browser. The tokens are saved in `data\secrets\` and refreshed automatically.
  Test with `python -m server.llm test siwc`.
- **OpenRouter** works here too: set the `OPENROUTER_API_KEY` environment variable and add `"openrouter"` to
  `ai_providers` in `data\settings.json`.
- Order of use: `ai_providers` (default `["claude_cli", "siwc"]`). If a provider fails or hits its limit, the next one
  is tried. If all fail, the engine's own words are used.

Consumer subscriptions are personal. Tick only the account holder unless your plan's terms allow more. See DECISIONS.md, D5.

## 6. Backups and restore

- Automatic: a consistent copy of `data\chess.db` every 24 hours in `data\backups\` (14 kept). Family settings →
  "Back up now" makes one on demand. Copy that folder to another machine now and then.
- Restore: stop the task, run
  `python -c "from server import store; from pathlib import Path; store.restore(Path(r'data\backups\chess-YYYYMMDD-HHMMSS.db'))"`
  in `C:\chess-companion`, then start the task again.

## 7. Recovery checklist

| Symptom | Fix |
|---|---|
| "The chess computer is not answering" | The PC is off or asleep, or the backend stopped. Check the task and `supervisor.log`. On the website it can also mean the relay isn't polling: check `server.log`. |
| Forgot the family password | `python -c "from server import auth; auth.set_password('family_pw', 'NEW')"` in `C:\chess-companion` (same for `admin_pw`). |
| A lost phone | Family settings → Devices → Remove. |
| AI wording missing | A provider is unavailable (limit reached, signed out, no credit). `server.log` says which. It retries after 5 minutes. |
| Power cut | Nothing to do: the task starts again, SQLite recovers, and the relay reconnects by itself. |
| Moving to a new PC | Install as in step 1, copy `data\` and the relay key, then run step 2. |
