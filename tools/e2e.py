"""End-to-end check through the website's api.php (exactly the path a phone uses).

  python tools/e2e.py http://localhost/chess-companion/ <family-password-file>
Logs in as a new device, creates (or reuses) the player "E2E Test", plays a whole game with
random legal moves, asks all six coach questions, exports the PGN and checks the history.
"""
import io
import json
import random
import sys
import time
import urllib.request

import chess
import chess.pgn

base = sys.argv[1].rstrip("/") + "/api.php"
pw = open(sys.argv[2], encoding="utf-8").read().split("family=")[1].split()[0]
tok = None


def api(op, **kw):
    req = urllib.request.Request(base, json.dumps({"op": op, **kw}).encode(),
                                 {"Content-Type": "application/json", "X-CC-Token": tok or ""})
    t = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        out = json.load(r)
    out["_ms"] = int((time.time() - t) * 1000)
    if not out["ok"]:
        raise SystemExit(f"{op} failed: {out}")
    return out


tok = api("login", password=pw, device="e2e script")["token"]
profs = api("profiles")["profiles"]
p = next((x for x in profs if x["name"] == "E2E Test"), None) or api("profile_create", name="E2E Test", age_group="teen")["profile"]
g = api("game_new", pid=p["id"], color=random.choice(["white", "black"]))["game"]
games_before = api("history", pid=p["id"])["stats"]["games"]   # after game_new, which may resign an old game
print(f"game {g['id']} as {g['color']} vs level {g['level']}")
rng = random.Random(7)
asked, times = set(), []
while g["status"] == "playing":
    b = chess.Board(g["fen"])
    if len(g["moves"]) >= 6 and len(asked) < 6:
        kind = ["hint", "danger", "winning", "plan", "explain", "best"][len(asked)]
        a = api("coach", gid=g["id"], kind=kind)
        asked.add(kind)
        print(f"  coach {kind:8} {a['_ms']:5}ms [{a['source']}] {a['text'][:90]}")
    # a weak player: prefers captures, otherwise random
    moves = list(b.legal_moves)
    caps = [m for m in moves if b.is_capture(m)]
    mv = rng.choice(caps if caps and rng.random() < 0.7 else moves)
    time.sleep(0.4)                     # stay under the 120 requests/minute limit, like a person would
    r = api("move", gid=g["id"], uci=mv.uci())
    times.append(r["_ms"])
    g = r["game"]
print(f"finished: {g['result']} ({g['termination']}), {len(g['moves'])} plies; move round-trip avg "
      f"{sum(times) / len(times):.0f}ms max {max(times)}ms")
pgn = api("pgn", gid=g["id"])["pgn"]
game = chess.pgn.read_game(io.StringIO(pgn))
assert len(list(game.mainline_moves())) == len(g["moves"]), "PGN move count mismatch"
h = api("history", pid=p["id"])
assert h["stats"]["games"] == games_before + 1, "history did not count the game"
print(f"PGN ok ({len(pgn)} bytes); history games {games_before} -> {h['stats']['games']}; "
      f"next opponent level {h['stats']['opponent_level_now']}")
print("E2E OK")
