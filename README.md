# Chess Companion

*An opponent that adapts. A coach that listens. A game that grows with you.*

A family chess app for phones, tablets and computers. It runs in the browser, so there is nothing to install on the
devices. The opponent adjusts its strength until each player wins about as often as they chose (default 65%).
A spoken coach answers six questions, always grounded in Stockfish analysis. It works for a young beginner
and for an experienced adult, and each gets their own profile.

- **[Run with Docker](#run-with-docker)**: one container, the coach worded by AI through your OpenRouter key.
- **[Home server + website](DEPLOY.md)**: a Windows PC runs the engine, and your existing web hosting serves the page
  through a small PHP relay (no ports opened). The AI wording comes from a Claude or ChatGPT subscription.
- Why it is built this way, with test and simulation results: [DECISIONS.md](DECISIONS.md)

## Features

| Area | What you get |
|---|---|
| Family access | One family password, checked on the server. Each device is remembered (a random token; only its hash is stored). The admin can remove devices. 10 wrong passwords lock that address for 15 minutes. |
| Banning | `admin.html` lists every device (name, phone/tablet/computer kind, last address, last seen) and the last 30 days of sign-ins, including wrong passwords. Ban a device, its address, or a whole address range. A banned device sees "This device has been blocked". No password is needed on the server itself; from anywhere else it asks for the admin password. The server's own address can never be banned. |
| Players | Pick or create a player: a first name or nickname and Child / Teen / Adult. Nothing else is collected. Each player has their own games, level, habits and settings. |
| Adaptive opponent | No rating needed. Strength is estimated from results (and, for the first games, from move quality), and the opponent level is set so the expected win rate equals your target (10–90%, default 65%). Changes are gradual. Help (hints, best move, danger, plan, takebacks) counts for less. |
| Human-like play | The opponent searches shallowly and picks among the top 10 moves with a strength-dependent randomness, preferring natural moves (captures, checks). Its mistakes are the kind people make, which leaves tactics for you to find. No result is decided in advance. |
| Coach | Six buttons: *Give me a hint* (gets stronger if you press again: idea → piece → square), *Am I in danger?*, *Am I winning?*, *What is my plan?*, *Explain the last move*, *Show me the best move*. Spoken (British English voice), with arrows and circles on the board. Silent unless asked. |
| Move stars | After every move: ★1–5 on screen (not spoken). On 1–2 stars the Undo button lights up, so the student can look for a better move. |
| After a hint | The move played after a hint gets a spoken verdict: "you found it", "just as good", or which move was better and why. |
| End of game | A report card: ★1–5 for how well you played (not just the result, with a bonus star for beating your recent average), the best moment of the game, and one tip. Read aloud. |
| AI wording | The engine's facts are reworded for the player's age and level by an AI model (OpenRouter in Docker; Claude Code or Sign in with ChatGPT in home-server mode). Answers that mention squares not in the engine facts are thrown away. Without AI, or when the limit is reached, the coach uses the engine's own wording, which always works. |
| Learning | Every move is graded. Recurring habits (leaving pieces loose, missing captures or checkmates, early queen, king left in the centre) are tracked with fading weights, shown in the history and given to the coach. |
| Progress | History shows the win rate against the target, and a line of the opponent strength needed to keep that win rate. A rising line means you are improving. |
| Game | New game (White, Black, surprise), automatic resume, undo, resign, promotion choice, PGN export at any point, game history with PGN per game. |
| Screen | The board fills the screen; big buttons; high-contrast pieces; sound on/off; repeat last advice. Installable as an app (PWA). |

## Run with Docker

You need Docker (with Compose) and, for the AI wording, an [OpenRouter API key](https://openrouter.ai/keys).
Without a key the app still works fully, with the engine's own coaching words.

```bash
git clone https://github.com/JosephAtwoosh/chess-companion.git
cd chess-companion
cp .env.example .env        # then edit .env: two passwords and OPENROUTER_API_KEY
docker compose up -d --build
```

Open <http://localhost:8765> (or `http://<this-computer's-address>:8765` from a phone on the same Wi-Fi),
type the family password, and create a player.

| Setting (`.env`) | Meaning |
|---|---|
| `CC_FAMILY_PASSWORD`, `CC_ADMIN_PASSWORD` | Used on the very first start only. Change them later in the app (☰ → Family settings). |
| `OPENROUTER_API_KEY` | Your key. Kept only in the container's environment, never written to disk or sent to the browser. `OPENROUTER_API_KEY_FILE` also works (Docker secrets). |
| `OPENROUTER_MODEL` | Default `anthropic/claude-haiku-5.5` (about $0.0001 per coach answer). Any OpenRouter chat model works. |
| `CC_AI_PROFILES` | `all` (default), or player ids. You can also tick players in Family settings. |
| `CC_AI_PER_DAY`, `CC_AI_PER_PROFILE_PER_HOUR` | Spending caps in requests (default 150 a day, 20 per player per hour). At 150 a day the worst case is about $0.45 a month with the default model. Set a credit limit on the key at OpenRouter too. |
| `CC_TRUST_PROXY=1` | Set this if a reverse proxy in front sets `X-Forwarded-For`. |

Data (players, games, backups, logs) lives in the `chess-data` volume (`/data`). A copy of the database is made every
24 hours in `/data/backups` (14 kept).

**Playing from outside your home:** don't expose port 8765 straight to the internet without HTTPS. Put it behind a
reverse proxy with a certificate (Caddy, nginx, Traefik) or a tunnel (for example Cloudflare Tunnel or Tailscale).
Then set `CC_TRUST_PROXY=1`, so the login lock-out sees real client addresses.

**Updating:** `git pull && docker compose up -d --build`.

## Using it

1. On each phone or tablet, type the family password once and name the device.
2. Tap your name (or **+ New player**), choose White or Black, and play by tapping a piece and then a square (or dragging).
3. Press a coach button whenever you want help. **Repeat** says it again. 🔊 switches sound off.
4. Devices and bans: open `admin.html` (e.g. <http://localhost:8765/admin.html>).
5. ☰ menu: New game, Challenge (target win rate), Move stars on/off, Game history, Export PGN, Resign, Change player,
   Family settings (admin password: devices, which players get AI wording, backup).

## Known limitations

- **The opponent's level is its own scale**, roughly Elo-like but not a FIDE or lichess rating.
- **Extreme targets converge slowly.** At 90%, a simulated player reached 80% over 60 games, and it keeps creeping
  towards the target.
- The weakest opponent level is 100. A complete beginner may still find it hard at first; the coach and undo help.
- Speech uses the device's built-in voices, so quality varies. On iOS the ringer switch must be on.
- The Docker image uses Debian's Stockfish package, which is older than the newest release but far stronger than any
  coaching needs.

## Tests

```bash
pip install -r requirements.txt
python -m unittest discover tests
```

Set `CC_STOCKFISH` to a Stockfish binary if it isn't at `engine/stockfish(.exe)` or on `PATH`. The tests cover rules,
undo, promotion, persistence, PGN, authentication and lock-out, device removal, rating maths, controller convergence,
all six coach answers, hints not giving the answer, AI grounding, OpenRouter (against a fake server), move stars,
the after-hint verdict, the report card, backups, and the website relay (needs `php`).
`tools/e2e.py <url> <password-file>` plays a whole game against a running server. GitHub Actions runs the tests,
builds the Docker image, starts it and plays a game against it on every push.

## Licence

GPL-3.0 (see [LICENSE](LICENSE)), because the app bundles [chessground](https://github.com/lichess-org/chessground)
(GPL-3.0) and uses [python-chess](https://github.com/niklasf/python-chess) and [Stockfish](https://stockfishchess.org)
(both GPL-3.0). [chess.js](https://github.com/jhlywa/chess.js) is BSD-2-Clause. The licence texts are in `web/vendor/`
and `engine/`.
