"""Core tests: rules, persistence, PGN, auth, adaptation, coach grounding.  python -m unittest discover tests
Uses a throw-away data folder and the real Stockfish binary (engine/stockfish.exe)."""
import io
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="cc-test-")
os.environ["CC_DATA"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess  # noqa: E402
import chess.pgn  # noqa: E402

from server import app, auth, coach, config, games, llm, opponent, rating, store  # noqa: E402

config.save({"ai_providers": []})          # tests never call a language model unless they mock one


def call(op, token=None, ip="127.0.0.1", **kw):
    return app.respond({"op": op, **kw}, token, ip)


def adm(action, **kw):          # the admin page, from the server itself
    return app.respond({"op": "local_admin", "action": action, **kw}, None, "127.0.0.1")


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = call("setup", family="family-pass", admin="admin-pass")
        cls.tok = r["token"]
        cls.pid = call("profile_create", cls.tok, name="Tester", age_group="adult")["profile"]["id"]

    # -------------------------------------------------- auth
    def test_auth(self):
        self.assertFalse(call("profiles")["ok"])                              # no token
        self.assertEqual(call("setup", family="x" * 8, admin="y" * 8)["error"], "Already set up.")
        self.assertFalse(call("login", ip="9.9.9.9", password="nope")["ok"])
        t = call("login", ip="9.9.9.8", password="family-pass", device="phone")["token"]
        self.assertTrue(call("profiles", t)["ok"])
        dev = store.one("SELECT id FROM devices WHERE token_hash=?", (auth.tok_hash(t),))
        self.assertTrue(call("admin", t, admin_password="admin-pass", action="revoke", device_id=dev["id"])["ok"])
        self.assertEqual(call("profiles", t)["error"], "auth")                # revoked device is out
        for _ in range(10):
            call("login", ip="6.6.6.6", password="bad")
        self.assertIn("Too many", call("login", ip="6.6.6.6", password="family-pass")["error"])
        self.assertFalse(call("admin", self.tok, admin_password="family-pass", action="overview")["ok"])

    def test_bans(self):
        ua = "Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile"
        t = app.respond({"op": "login", "password": "family-pass", "device": "visitor"}, None, "5.5.5.5", ua)["token"]
        dev = store.one("SELECT * FROM devices WHERE token_hash=?", (auth.tok_hash(t),))
        self.assertEqual((dev["last_ip"], dev["ua"]), ("5.5.5.5", ua))
        app.respond({"op": "profiles"}, t, "5.5.5.6")                          # moved address: noted
        self.assertEqual(store.one("SELECT last_ip FROM devices WHERE id=?", (dev["id"],))["last_ip"], "5.5.5.6")
        # the admin page: free on the server, admin password elsewhere, never through the website relay
        self.assertTrue(adm("overview")["ok"])
        self.assertFalse(call("local_admin", ip="5.5.5.7", action="overview")["ok"])
        self.assertTrue(call("local_admin", ip="5.5.5.7", action="overview", admin_password="admin-pass")["ok"])
        self.assertFalse(app.respond({"op": "local_admin", "action": "overview"}, None, "127.0.0.1", "", True)["ok"])
        # device ban: refused at once, and also after typing the password again on it
        self.assertTrue(adm("ban_device", device_id=dev["id"])["ok"])
        self.assertEqual(call("profiles", t, ip="5.5.5.6")["error"], "blocked")
        self.assertTrue(call("status", t, ip="5.5.5.6")["blocked"])
        self.assertEqual(call("login", t, ip="5.5.5.6", password="family-pass")["error"], "blocked")
        self.assertTrue(call("profiles", self.tok)["ok"])                       # others unaffected
        adm("unban_device", device_id=dev["id"])
        self.assertEqual(call("profiles", t, ip="5.5.5.6")["error"], "auth")    # unbanned: needs the password again
        # address and range bans
        self.assertTrue(adm("ban_ip", ip=" 7.7.7.0/24 ", note="test")["ok"])
        self.assertEqual(call("login", ip="7.7.7.9", password="family-pass")["error"], "blocked")
        self.assertTrue(call("login", ip="7.7.8.9", password="family-pass")["ok"])
        for bad in ("127.0.0.1", "127.0.0.0/8", "0.0.0.0/0", "not an ip"):
            self.assertFalse(adm("ban_ip", ip=bad)["ok"], bad)
        self.assertIn("using now", app.respond({"op": "local_admin", "admin_password": "admin-pass", "action": "ban_ip",
                                                "ip": "7.7.8.0/24"}, None, "7.7.8.9")["error"])
        ov = adm("overview")
        self.assertEqual([b["ip"] for b in ov["bans"]], ["7.7.7.0/24"])
        self.assertEqual(ov["log"][0]["event"], "signed_in")
        self.assertIn("blocked", [r["event"] for r in ov["log"]])
        adm("unban_ip", ban_id=ov["bans"][0]["id"])
        self.assertTrue(call("login", ip="7.7.7.9", password="family-pass")["ok"])
        # ban a device together with its address
        t2 = call("login", ip="8.8.4.4", password="family-pass", device="x")["token"]
        adm("ban_device", device_id=auth.device_row(t2)["id"], with_address=True)
        self.assertEqual(call("login", ip="8.8.4.4", password="family-pass")["error"], "blocked")
        self.assertTrue(call("profiles", self.tok)["ok"])                       # the server is never banned

    def test_setup_only_local(self):
        os.environ["CC_DATA"] = TMP
        self.assertFalse(auth.is_local("81.2.3.4"))

    # -------------------------------------------------- rules and persistence
    def test_rules(self):
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]
        self.assertTrue(g["your_turn"])
        bad = call("move", self.tok, gid=g["id"], uci="e2e5")
        self.assertFalse(bad["ok"])
        ok = call("move", self.tok, gid=g["id"], uci="e2e4")["game"]
        self.assertEqual(ok["moves"][0], "e4")
        self.assertEqual(len(ok["moves"]), 2)                                 # computer replied
        b = chess.Board(ok["fen"])
        self.assertTrue(b.turn == chess.WHITE and b.is_valid())
        # persistence: the stored game rebuilds to the same position
        self.assertEqual(games.board_of(games.get(g["id"])).fen(), ok["fen"])
        # undo takes back both moves
        u = call("undo", self.tok, gid=g["id"])["game"]
        self.assertEqual(u["moves"], [])
        self.assertEqual(games.get(g["id"])["takebacks"], 1)

    def test_black_and_promotion(self):
        g = call("game_new", self.tok, pid=self.pid, color="black")["game"]
        self.assertEqual(len(g["moves"]), 1)                                  # computer opened as White
        # promotion without a piece letter becomes a queen
        gg = games.get(g["id"])
        b = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
        self.assertIn(chess.Move.from_uci("a7a8q"), b.legal_moves)

    def test_pgn(self):
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]
        call("move", self.tok, gid=g["id"], uci="d2d4")
        p = call("pgn", self.tok, gid=g["id"])["pgn"]
        game = chess.pgn.read_game(io.StringIO(p))
        self.assertEqual(game.headers["White"], "Tester")
        self.assertEqual(len(list(game.mainline_moves())), 2)

    def test_finish_and_rating(self):
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]   # may resign an earlier test's game
        p0 = games.profile(self.pid)
        for u in ("e2e4", "d2d4", "g1f3", "f1c4"):
            st = call("move", self.tok, gid=g["id"], uci=u)
            if not st["ok"]:
                b = chess.Board(games.board_of(games.get(g["id"])).fen())
                call("move", self.tok, gid=g["id"], uci=next(iter(b.legal_moves)).uci())
        r = call("resign", self.tok, gid=g["id"])["game"]
        self.assertEqual(r["status"], "finished")
        card = r["review"]["rating"]                                          # end-of-game report card
        self.assertTrue(1 <= card["stars"] <= 5 and card["text"].startswith(card["headline"]))
        self.assertEqual(r["score"], 0.0)
        p1 = games.profile(self.pid)
        self.assertLess(p1["rating"], p0["rating"])                           # a loss lowers the level
        self.assertEqual(p1["games"], p0["games"] + 1)
        h = call("history", self.tok, pid=self.pid)
        self.assertTrue(any(x["id"] == g["id"] for x in h["games"]))

    # -------------------------------------------------- adaptation
    def test_rating_math(self):
        self.assertAlmostEqual(rating.offset(0.5), 0)
        self.assertGreater(rating.opponent_level(1000, 0.3), rating.opponent_level(1000, 0.7))
        settled = lambda: {"changes": 25, "last": 1}
        up = rating.update(1000, 30, 0.65, 1.0, 1.0, None, 0, settled())
        down = rating.update(1000, 30, 0.65, 0.0, 1.0, None, 0, settled())
        self.assertLess(abs(up - 1000), 40)                                   # no big swings once settled
        self.assertLess(abs(down - 1000), 40)
        assisted = rating.update(1000, 30, 0.65, 1.0, 0.2, None, 0, settled())
        self.assertLess(assisted - 1000, up - 1000)                           # help counts for less

    def test_controller_converges(self):
        rng = random.Random(3)
        for true, target in ((600, 0.65), (1400, 0.3), (1000, 0.8)):
            r, wins, n, adapt = rating.START["adult"], 0.0, 0, {}
            for gi in range(300):
                L = rating.opponent_level(r, target)
                s = 1.0 if rng.random() < 1 / (1 + 10 ** ((L - true) / 400)) else 0.0
                r = rating.update(r, gi, target, s, 1.0, None, 0, adapt)
                if gi >= 100:
                    wins += s; n += 1
            self.assertAlmostEqual(wins / n, target, delta=0.07, msg=f"{true} {target}")

    def test_opponent_strength_params(self):
        d1, t1, *_ = opponent.params(300)
        d2, t2, *_ = opponent.params(2000)
        self.assertLess(d1, d2)
        self.assertGreater(t1, t2)
        b = chess.Board()
        for lvl in (100, 1200, 2600):
            mv, _ = opponent.choose(b, lvl, random.Random(1))
            self.assertIn(mv, b.legal_moves)

    # -------------------------------------------------- coach
    def _game_after(self, ucis):
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]
        gg = games.get(g["id"])
        moves = []
        b = chess.Board()
        for u in ucis:
            mv = chess.Move.from_uci(u)
            moves.append({"u": u, "s": b.san(mv), "by": "p" if b.turn == chess.WHITE else "o", "cp": 0, "best": None})
            b.push(mv)
        store.run("UPDATE games SET moves=? WHERE id=?", (json.dumps(moves), gg["id"]))
        return games.get(gg["id"])

    def test_coach_all_kinds_engine(self):
        # after 1.e4 e5 2.Bc4 Qh4 Black threatens Qxe4+ (Stockfish's view): danger must be reported
        g = self._game_after(["e2e4", "e7e5", "f1c4", "d8h4"])
        p = games.profile(self.pid)
        for k in coach.KINDS:
            a = coach.answer(k, g, p, 1)
            self.assertTrue(a["text"], k)
            self.assertEqual(a["source"], "engine")
        d = coach.answer("danger", g, p)
        self.assertTrue(d["text"].startswith("Yes"))
        self.assertEqual(d["shapes"][0]["orig"], "h4")
        best = coach.answer("best", g, p)
        b = games.board_of(g)
        import chess.engine
        from server.engine import pool
        top = pool().analyse(b, chess.engine.Limit(time=0.8), multipv=3)     # equal moves may swap places
        self.assertIn(best["shapes"][0]["dest"], {chess.square_name(i["pv"][0].to_square) for i in top})

    def test_hint_does_not_give_answer(self):
        g = self._game_after(["e2e4", "e7e5", "g1f3", "b8c6"])
        p = games.profile(self.pid)
        h = coach.answer("hint", g, p, 1)
        self.assertNotRegex(h["text"], r"\b[a-h][1-8]\b")                     # no squares at level 1

    def test_llm_grounding(self):
        g = self._game_after(["e2e4", "e7e5", "g1f3", "b8c6"])
        p = games.profile(self.pid)
        saved = dict(llm.PROVIDERS)
        try:
            config.save({"ai_providers": ["claude_cli"], "ai_subscription_profiles": [p["id"]]})
            llm.PROVIDERS["claude_cli"] = lambda s, q, c: "Move your bishop to h7 now!"   # invented square
            self.assertEqual(coach.answer("winning", g, p)["source"], "engine")
            coach._down.clear()
            llm.PROVIDERS["claude_cli"] = lambda s, q, c: "It's about equal, keep developing."
            self.assertEqual(coach.answer("winning", g, p)["source"], "claude_cli")
            # subscription providers are only for the profiles the admin ticked
            config.save({"ai_providers": ["claude_cli"], "ai_subscription_profiles": []})
            self.assertEqual(coach.providers_for(p, config.load()), [])
            config.save({"ai_subscription_profiles": [p["id"]]})
            self.assertEqual(coach.providers_for(p, config.load()), ["claude_cli"])
        finally:
            llm.PROVIDERS.update(saved)
            config.save({"ai_providers": [], "ai_subscription_profiles": []})

    def test_assist_flag(self):
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]
        call("coach", self.tok, gid=g["id"], kind="best")
        st = call("move", self.tok, gid=g["id"], uci="e2e4")
        mv = json.loads(games.get(g["id"])["moves"])[0]
        self.assertTrue(mv["a"])                                              # helped move is marked

    def test_feedback_after_hint(self):
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]
        r = call("move", self.tok, gid=g["id"], uci="e2e4")
        self.assertNotIn("feedback", r)                                       # silent without a hint
        call("coach", self.tok, gid=g["id"], kind="hint")
        hinted = json.loads(games.get(g["id"])["summary"])["hint"]["best"]
        r = call("move", self.tok, gid=g["id"], uci=hinted)                  # play the hinted move
        self.assertIn("well done", r["feedback"]["text"].lower())
        # a hint, then a poor move (a pawn on the edge): the coach answers, naming a better move if it was worse
        call("coach", self.tok, gid=g["id"], kind="hint")
        best = json.loads(games.get(g["id"])["summary"])["hint"]["best"]
        b = games.board_of(games.get(g["id"]))
        poor = next(m for m in ("a2a3", "h2h3", "a2a4", "h2h4") if chess.Move.from_uci(m) in b.legal_moves and m != best)
        r = call("move", self.tok, gid=g["id"], uci=poor)
        self.assertTrue(r["feedback"]["text"])
        if "good move" not in r["feedback"]["text"].lower():
            self.assertRegex(r["feedback"]["text"], r"(better|stronger)")
        b = games.board_of(games.get(g["id"]))
        r = call("move", self.tok, gid=g["id"], uci=next(iter(b.legal_moves)).uci())
        self.assertNotIn("feedback", r)                                       # one hint, one verdict

    def test_move_stars(self):
        self.assertEqual(games.move_stars(0, True), 5)
        self.assertEqual(games.move_stars(4, False), 4)
        self.assertEqual(games.move_stars(40, False), 1)
        g = call("game_new", self.tok, pid=self.pid, color="white")["game"]
        r = call("move", self.tok, gid=g["id"], uci="e2e4")["game"]
        self.assertGreaterEqual(r["last_rating"]["stars"], 4)                 # 1.e4 is a fine move
        self.assertEqual(r["last_rating"]["san"], "e4")
        b = chess.Board(r["fen"])
        # give the queen away if Black's reply allows it; otherwise any move must still get 1-5 stars
        bad = next((m for m in b.legal_moves if b.piece_at(m.from_square).piece_type == chess.QUEEN
                    and b.is_attacked_by(chess.BLACK, m.to_square) and not b.is_attacked_by(chess.WHITE, m.to_square)), None)
        r2 = call("move", self.tok, gid=g["id"], uci=(bad or next(iter(b.legal_moves))).uci())["game"]
        if bad:
            self.assertLessEqual(r2["last_rating"]["stars"], 2)
        self.assertIn(r2["last_rating"]["stars"], range(1, 6))

    def test_openrouter(self):
        import http.server, threading
        seen = {}
        status = {"code": 200}

        class Fake(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen["auth"] = self.headers.get("Authorization")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if status["code"] != 200:
                    self.send_response(status["code"]); self.end_headers(); self.wfile.write(b'{"error":"no credits"}'); return
                out = json.dumps({"choices": [{"message": {"content": "You are doing well, keep going."}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        g = self._game_after(["e2e4", "e7e5"])
        p = games.profile(self.pid)
        try:
            config.save({"ai_providers": ["openrouter"], "ai_profiles": "all",
                         "openrouter_url": f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"})
            coach._down.clear()
            a = coach.answer("winning", g, p)
            self.assertEqual(a["source"], "openrouter")
            self.assertEqual(seen["auth"], "Bearer sk-or-test")
            self.assertEqual(seen["body"]["model"], config.load()["openrouter_model"])
            status["code"] = 402                                              # out of credits -> engine words
            coach._down.clear()
            self.assertEqual(coach.answer("winning", g, p)["source"], "engine")
        finally:
            srv.shutdown()
            del os.environ["OPENROUTER_API_KEY"]
            config.save({"ai_providers": [], "ai_profiles": []})

    def test_backup(self):
        f = store.backup()
        self.assertTrue(f.exists() and f.stat().st_size > 0)


def tearDownModule():
    from server.engine import pool
    pool().close()


if __name__ == "__main__":
    unittest.main()
