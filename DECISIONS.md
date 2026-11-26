# Decisions and lessons learned

For future maintainers and AI agents. Newest at the bottom of each section. Dates are 2026.

## D1. Architecture: the home server does everything; the website is a mailbox (26 Nov)

- The website is shared PHP hosting (Linux) and the home server must not be exposed. So the browser talks only to
  `chess-companion/api.php`. The home server's backend **polls** that file over HTTPS (long-poll, 20 s) and posts answers back.
  This reuses the pattern of an existing family site (`api.php` plus a key whose SHA-256 is on the site), so there is
  no new service (no tunnel, no port forwarding, no VPN).
- On the home server itself (`PHP_OS_FAMILY === 'Windows'`), `api.php` forwards straight to `127.0.0.1:8765`, so
  <http://localhost/chess-companion/> needs no relay. Override with `data/relay.json` `mode` or the `CC_RELAY_MODE` env variable.
- Backend: Python stdlib HTTP server + python-chess + SQLite. No framework, no Node build, no Docker.
- The front end has no build step: vendored ES modules `chessground` 9.2.1 (lichess's board: tap/drag, arrows, circles;
  GPL-3.0) and `chess.js` 1.4.0 (BSD-2) for legal-move dots. The server stays the authority on the rules.
- Lesson: PHP's built-in server is single-threaded (and `PHP_CLI_SERVER_WORKERS` does not work on Windows), so a long-poll
  blocks everything else on that port. Real Apache/PHP-FPM is fine. The relay test therefore uses three PHP servers
  (browser side, poll, reply). `relay_reply_url` exists only for that test.
- Lesson: from Python on Windows, `http://localhost` costs about 2 s per request (it tries `::1` first, PHP listens on IPv4).
  Use `127.0.0.1` in scripts. Browsers are not affected.

## D2. Engine choice: Stockfish 19 only (26 Nov)

- Considered **Maia** (human-like nets for lc0): it plays like 1100–1900 humans but needs lc0 plus nets, has nothing
  below 1100 (too strong for a young child), and gives no continuous strength dial. Stockfish's own
  `UCI_LimitStrength` starts at 1320, and `Skill Level` jumps are coarse and its blunders feel random.
- Chosen: **one Stockfish 19** binary (CPU, about 100 MB, official release) used three ways:
  1. the opponent: a shallow MultiPV search with a strength-dependent softmax (below),
  2. grading each player move (0.12 s full strength),
  3. the coach (0.8 s full strength, MultiPV 3, plus a "null move" search to find threats).
  The coach's analysis never depends on the opponent's strength.
- Pool of 3 engine processes, 2 threads each. Moves take about 0.3 s and coach answers about 1.3 s on a 12-core Xeon E5-2680 v3.

## D3. The opponent's move picker (`server/opponent.py`)

level x∈[0,1] (100 → 2800): depth = 1 + 15·x^1.3, temperature T = 0.4 + 30·(1−x)^2.5 win-% points,
preference for captures/checks/moving attacked pieces = 1.2·(1−x)², "careless natural move" chance = 10%·(1−x)³.
Every move is drawn afresh, and nothing about a game's result is fixed in advance. The shallow search is what makes the
mistakes human: a weak level misses a two-move tactic instead of playing randomly.

## D4. Adaptation (`server/rating.py`)

- R = the player's strength on the opponent's level scale. Opponent level L = R − 400·log10(p/(1−p)).
  After a game: R += K·w·(score − p), where w is the share of unassisted moves (hint, best move, danger, plan and undo all
  mark the next move as assisted). This is a Robbins–Monro search for the level where the player scores p. It
  converges to the target even though our level scale is not a true Elo scale.
- **Kesten's rule** for K (added after the first simulations): K = max(32, 160/√(1+c)), where c counts how often the
  result flipped between above and below target. A run of one-sided results keeps K large. Before this, simulated
  players of level 250–400 needed about 60 games to come down from the adult start of 1100. After it, they needed about 6–26.
- First-games hint from move quality: the "balanced loss" (mean win-chance drop over moves made while the position
  was 25–75%) maps to a level (`CALIBRATION`), blended in with a weight of 0.35 that fades out by game 6.
  Lesson: lichess-style *accuracy* does not separate levels here. A level-100 picker scored 89% and level 1000 scored 82%,
  because once a game is lost every move looks accurate. Even balanced loss is flat below level 1000
  (100: 8.4, 300: 9.3, 600: 7.3, 1000: 7.1, 1500: 3.8, 2000: 1.8; 8 games each), so its weight is kept small.
- Starting estimates: child 500, teen 900, adult 1100. Nothing else is asked.
- A target change applies from the next game (or immediately if the current game has at most one move each).
- New game during a game: with 4 or more of your moves it counts as a resignation (the UI warns). Otherwise it is
  abandoned and not counted.

### Simulation results (real Stockfish games, `tools/simulate.py`, adult start 1100, 60 games each)

Simulated player = the same picker at a fixed hidden level. Adjudication: 8+ points ahead for 10 plies after
move 30 counts as a win. 240 plies is a draw.

| simulated player level | target | win rate, games 16–60 | opponent level, game 1 → 60 | settled by game |
|---|---|---|---|---|
| 250 | 65% | 57% | 992 → 143 | 26 |
| 400 | 65% | 62% | 992 → 245 | 6 |
| 1000 | 30% | 36% | 1247 → 1232 | 10 |
| 1000 | 65% | 64% | 992 → 762 | 5 |
| 1000 | 90% | 80% | 718 → 598 | 29 (still creeping) |
| 1700 | 65% | 68% (40 games) | 992 → 1650 | 12 |

The controller alone, with a logistic player and 333 runs × 60 games (`simulate.py --model`), lands within 1–5
points of every target from 30% to 90%. The largest single-game change after game 20 is 40–80 level points.

## D5. Subscription AI (investigated 26 Nov)

**Rule: the language model only rewords engine facts.** It receives only the facts that question may reveal
(a hint never contains the move), and any answer that names a square not in those facts is thrown away and the engine's
wording is used instead. Arrows and circles always come from the engine.

- **Claude, chosen and proven.** Claude Code is installed and signed in on the home server. Its headless mode
  (`claude -p --model haiku --tools "" --no-session-persistence --setting-sources "" --system-prompt …`) is an
  officially supported, documented use of Claude Code with the subscription. The proof of concept answered a coaching
  prompt in 6 s. `--bare` cannot be used, because it requires an API key. Anthropic does not allow offering claude.ai
  login or limits to *other people's* products, and the consumer subscription is personal. So it is enabled per player
  by the admin, **default nobody**, and intended for the account holder.
- **ChatGPT: Sign in with ChatGPT "plan usage" is implemented but unproven** (it needs the account holder's
  interactive sign-in). From developers.openai.com/siwc: plan usage lets an app make Responses-API requests counted
  against the user's Plus or Pro plan. It is available to open-source projects and "personal projects that run locally".
  Remotely hosted or paid apps need the waitlist. Plus has a five-hour usage window shared with other apps. When the
  limit is reached it returns 429 `subscription_sharing_usage_limit_exceeded` and never silently bills the API.
  OAuth uses a public client with PKCE and a loopback redirect `http://127.0.0.1:1455/auth/callback`
  (`client_id=dynamic_agent_client` first, then the issued `oaiapp_…` id), scopes
  `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`, `resource=https://api.openai.com/v1`.
  Access tokens last 1 h. Refresh tokens last 30 days and rotate. Our backend runs on the home server and is reached through
  our own relay, so we treat it as a local personal project for the account holder only. Family use by
  other people is not addressed by the docs, so it is not enabled for them.
- Neither provider reads browser cookies or shares credentials. No API key is used anywhere, so a metered charge cannot happen.
- **Local llama.cpp: removed (26 Nov) at the owner's request.** Before removal it worked
  (about 4 s on an idle CPU, and over 20 s while simulations ran). Other players get the engine's own wording, which is
  instant and always available.
- Limits: 20 AI requests per player per hour and 150 per day overall. A failing provider is skipped for 5 minutes.

## D6. Privacy and security

- Profiles: a first name or nickname and an age group only. No email, birthday or photo.
- Passwords: PBKDF2-SHA256 with 200k rounds. Device tokens: 256-bit, stored as SHA-256, revocable. Admin password is separate.
- First-time setup works only from the home server (the client IP must be loopback; the website relay passes the real client IP).
- The backend listens on 127.0.0.1 only. AI tokens stay in `data/secrets/` on the home server. Content Security Policy:
  `script-src 'self'`, no third-party scripts or fonts.

## D7. Local preview (26 Nov)

- `tools/dev-router.php` serves `web/` under `/chess-companion/` with PHP's built-in server and mirrors the `.htaccess`
  rules. It can be the router itself, or be included from another router that already owns port 80.
- Test data goes in a separate data folder (`run.py --data DIR`). The real `data/` starts empty, so the owner chooses the
  passwords.

## D8. Feedback while playing (26 Nov, user requests)

- **Move stars** (every move, shown but never spoken: speech after every move was too distracting): stars come from the drop in win chance from the 0.3 s full-strength check
  (`games.move_stars`): best move or a drop under 2 points = 5, under 6 = 4, under 12 = 3, under 22 = 2, otherwise 1.
  `quality_time` went from 0.12 s to 0.3 s so the stars are trustworthy. The coach only *suggests* Undo and never names
  the better move (that is what Best move is for). A taken-back move counts as assisted for the strength estimate.
- **After a hint**: the next move is re-checked with the hint's own 0.8 s analysis and gets a spoken verdict.
  It replaces the short star word, so there is no double speech.
- **Report card** (end of game, `coach.game_rating`): based on the share of good-or-best unassisted moves, +1 star for
  beating the player's average of the last 10 games by 5 points, and at least 3 stars for a win. It is meant to
  encourage, so it names the best moment and gives exactly one tip.
- Layout: the side-by-side layout now starts at aspect ratio 1:1 (was 5:4), so near-square desktop windows don't push
  the coach buttons below the fold.

## D9. Docker + OpenRouter (26 Nov)

- One image (`python:3.12-slim`, Debian's `stockfish`, python-chess). The backend serves the web app itself
  (`CC_SERVE_WEB=1`) and answers `api.php` requests directly, so there is no PHP and no relay.
- Config layers: defaults < environment variables < `data/settings.json` (changes made in the app). The OpenRouter key is
  read only from `OPENROUTER_API_KEY` / `OPENROUTER_API_KEY_FILE`, so it is never written to disk or logged.
- Passwords come from `CC_FAMILY_PASSWORD` / `CC_ADMIN_PASSWORD` on first start, because a container never sees a
  loopback client and so the on-device setup screen can't be used.
- Security fix that came with listening on the network: `X-CC-Client-IP` (set by the local PHP proxy) is trusted only
  from a loopback peer. Otherwise anyone could claim to be the server to unlock setup or dodge the login lock-out.
  `X-Forwarded-For` is used only with `CC_TRUST_PROXY=1`.
- OpenRouter is pay-per-use. It is used only when listed in `ai_providers` (the Docker image lists it), and is capped by
  `ai_per_day` (150) and `ai_per_profile_per_hour` (20). On 401/402/429 the coach falls back to engine words and skips
  the provider for 5 minutes. Default model `anthropic/claude-haiku-5.5` ($0.10/M input, $0.50/M output on 26 Nov): about
  $0.0001 per answer.
- Unified permission: `ai_profiles` ("all" or a list of ids; the admin's ticks) decides who gets AI wording for every
  provider. Older installs fall back to `ai_subscription_profiles`.
- No Docker on the development PC, so GitHub Actions is the Docker test bed: unit tests on Ubuntu, then build the image,
  start it with test passwords, wait for the healthcheck, and play a full game through `tools/e2e.py`.
