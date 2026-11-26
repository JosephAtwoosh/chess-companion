"""Games: start, move, undo, resign, finish, PGN. The server is the authority on the rules.

Each move is stored as a small dict:
  u  uci          s  SAN          by "p" (player) or "o" (opponent)
  cp evaluation after the move, White's point of view (quick full-strength look)
  best  engine's best move in the resulting position (uci)
  a  True if the player had help (hint / best move / danger / plan / takeback) before this move
  acc, cls, theme   grading of the player's moves (accuracy 0-100, good/inaccuracy/mistake/blunder)
"""
import io
import json
import random
import threading
import time
from collections import defaultdict

import chess
import chess.engine
import chess.pgn

from . import config, opponent, rating, store
from .engine import accuracy, cp_of, pool, win_chance

_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
ASSISTING = {"hint", "best", "danger", "plan"}
MAX_PLIES = 400


def quick_eval(board: chess.Board):
    """(cp White POV, best move uci or None) from a short full-strength search."""
    if board.is_game_over():
        o = board.outcome()
        if o.winner is None:
            return 0, None
        return (100000 if o.winner == chess.WHITE else -100000), None
    info = pool().analyse(board, chess.engine.Limit(time=config.load()["quality_time"]))[0]
    best = info["pv"][0].uci() if info.get("pv") else None
    return cp_of(info["score"], chess.WHITE), best


def board_of(g) -> chess.Board:
    b = chess.Board()
    for m in json.loads(g["moves"]) if isinstance(g["moves"], str) else g["moves"]:
        b.push_uci(m["u"])
    return b


def get(game_id: int):
    return store.one("SELECT * FROM games WHERE id=?", (game_id,))


def profile(pid: int):
    return store.one("SELECT * FROM profiles WHERE id=? AND deleted=0", (pid,))


def player_color(g) -> chess.Color:
    return chess.WHITE if g["color"] == "white" else chess.BLACK


def state(g) -> dict:
    moves = json.loads(g["moves"])
    b = board_of(g)
    summary = json.loads(g["summary"])
    p = profile(g["profile_id"])
    out = {
        "id": g["id"], "color": g["color"], "fen": b.fen(), "status": g["status"],
        "result": g["result"], "termination": g["termination"], "score": g["score"],
        "moves": [m["s"] for m in moves], "last": moves[-1]["u"] if moves else None,
        "turn": "white" if b.turn else "black", "check": b.is_check(),
        "level": round(g["level"]), "your_turn": g["status"] == "playing" and b.turn == player_color(g),
        "takebacks": g["takebacks"],
    }
    if g["status"] == "finished" and p:
        out["rating_change"] = {"before": round(rating.opponent_level(g["rating_before"], g["target"])),
                                "after": round(rating.opponent_level(g["rating_after"] or g["rating_before"], p["target"]))}
    out["review"] = summary.get("review")
    lastp = next((m for m in reversed(moves) if m["by"] == "p" and "stars" in m), None)
    out["last_rating"] = ({"stars": lastp["stars"], "label": STAR_WORDS[lastp["stars"]], "san": lastp["s"],
                           "ply": moves.index(lastp)} if lastp else None)
    return out


def _save(g, moves, **extra):
    sets = {"moves": json.dumps(moves), **extra}
    cols = ",".join(f"{k}=?" for k in sets)
    store.run(f"UPDATE games SET {cols} WHERE id=?", (*sets.values(), g["id"]))


def current(pid: int):
    return store.one("SELECT * FROM games WHERE profile_id=? AND status='playing' ORDER BY id DESC LIMIT 1", (pid,))


def new_game(pid: int, color: str, rng=random):
    p = profile(pid)
    if not p:
        raise ValueError("No such player")
    old = current(pid)
    if old:
        n_player = sum(1 for m in json.loads(old["moves"]) if m["by"] == "p")
        if n_player >= 4:
            resign(old["id"])  # counts as a loss (the UI warns first)
        else:
            store.run("UPDATE games SET status='abandoned', ended=?, result='*' WHERE id=?", (time.time(), old["id"]))
    if color not in ("white", "black"):
        color = rng.choice(["white", "black"])
    level = rating.opponent_level(p["rating"], p["target"])
    cp, best = quick_eval(chess.Board())
    summary = {"start": {"cp": cp, "best": best}, "hints": {}, "pending_assist": False}
    gid = store.run(
        "INSERT INTO games(profile_id,color,started,status,result,level,target,rating_before,summary) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, color, time.time(), "playing", "*", level, p["target"], p["rating"], json.dumps(summary)))
    g = get(gid)
    if color == "black":
        g = _engine_reply(g, rng)
    return g


def _engine_reply(g, rng=random):
    b = board_of(g)
    moves = json.loads(g["moves"])
    mv, info = opponent.choose(b, g["level"], rng)
    san = b.san(mv)
    b.push(mv)
    cp, best = quick_eval(b)
    moves.append({"u": mv.uci(), "s": san, "by": "o", "cp": cp, "best": best, "why": info.get("reason")})
    _save(g, moves)
    g = get(g["id"])
    return _check_end(g, b)


def _eval_before(g, moves, idx):
    """Engine view of the position before moves[idx]: (cp White POV, best uci)."""
    if idx == 0:
        s = json.loads(g["summary"])["start"]
        return s["cp"], s["best"]
    return moves[idx - 1]["cp"], moves[idx - 1]["best"]


def _grade(g, moves, idx, board_before: chess.Board):
    m = moves[idx]
    me = player_color(g)
    cp0, best0 = _eval_before(g, moves, idx)
    sign = 1 if me == chess.WHITE else -1
    wc0, wc1 = win_chance(sign * cp0), win_chance(sign * m["cp"])
    m["acc"] = round(accuracy(wc0, wc1), 1)
    m["wc"] = [round(wc0, 1), round(wc1, 1)]
    loss = wc0 - wc1
    m["cls"] = "blunder" if loss >= 30 else "mistake" if loss >= 20 else "inaccuracy" if loss >= 10 else "good"
    if m["u"] == best0:
        m["cls"] = "best"
    m["stars"] = move_stars(loss, m["cls"] == "best")
    if m["cls"] in ("mistake", "blunder"):
        theme = "other"
        if sign * cp0 > 90000 and sign * m["cp"] < 90000:
            theme = "missed_mate"
        elif m.get("best"):
            after = board_before.copy()
            after.push_uci(m["u"])
            reply = chess.Move.from_uci(m["best"])
            if after.is_capture(reply):
                theme = "hung_piece"
            elif after.gives_check(reply):
                theme = "missed_threat"
        if theme == "other" and best0:
            bm = chess.Move.from_uci(best0)
            if board_before.is_capture(bm) or board_before.gives_check(bm):
                theme = "missed_tactic"
        m["theme"] = theme


STAR_WORDS = {5: "Excellent move!", 4: "Good move.", 3: "OK move.",
              2: "Hmm, there was something better.", 1: "Oops, that's a mistake."}


def move_stars(loss: float, is_best: bool) -> int:
    """1-5 stars from the drop in win chance (percentage points) caused by the move."""
    if is_best or loss < 2:
        return 5
    return 4 if loss < 6 else 3 if loss < 12 else 2 if loss < 22 else 1


def move(game_id: int, uci: str, rng=random):
    with _locks[game_id]:
        g = get(game_id)
        if not g or g["status"] != "playing":
            raise ValueError("This game is over.")
        b = board_of(g)
        if b.turn != player_color(g):
            raise ValueError("Please wait for the computer's move.")
        try:
            mv = chess.Move.from_uci(uci)
        except ValueError:
            raise ValueError("That move is not allowed.")
        if mv not in b.legal_moves:
            # allow auto-queen if the client sent a pawn move to the last rank without a piece
            mvq = chess.Move(mv.from_square, mv.to_square, chess.QUEEN)
            if mvq in b.legal_moves:
                mv = mvq
            else:
                raise ValueError("That move is not allowed.")
        summary = json.loads(g["summary"])
        before = b.copy()
        moves = json.loads(g["moves"])
        san = b.san(mv)
        b.push(mv)
        cp, best = quick_eval(b)
        moves.append({"u": mv.uci(), "s": san, "by": "p", "cp": cp, "best": best,
                      "a": bool(summary.get("pending_assist"))})
        _grade(g, moves, len(moves) - 1, before)
        summary["pending_assist"] = False
        hint = summary.pop("hint", None)
        if hint and hint.get("ply") == len(moves) - 1:     # the hint was for this very position
            summary["feedback_request"] = {**hint, "ply": len(moves) - 1}
        _save(g, moves, summary=json.dumps(summary))
        g = _check_end(get(game_id), b)
        if g["status"] == "playing":
            g = _engine_reply(g, rng)
        return g


def mark_assist(game_id: int, hint: dict | None = None):
    """The next move had help. `hint` (best move + win chance from the hint's analysis) makes the
    coach comment on that next move (coach.move_feedback)."""
    g = get(game_id)
    if g and g["status"] == "playing":
        s = json.loads(g["summary"])
        s["pending_assist"] = True
        if hint:
            s["hint"] = {**hint, "ply": len(json.loads(g["moves"]))}
        store.run("UPDATE games SET summary=?, coach_uses=coach_uses+1 WHERE id=?", (json.dumps(s), game_id))


def undo(game_id: int):
    """Take back the player's last move (and the computer's reply)."""
    with _locks[game_id]:
        g = get(game_id)
        if not g or g["status"] != "playing":
            raise ValueError("This game is over.")
        moves = json.loads(g["moves"])
        while moves and moves[-1]["by"] == "o":
            moves.pop()
        if not moves:
            raise ValueError("Nothing to undo.")
        moves.pop()
        s = json.loads(g["summary"])
        s["pending_assist"] = True            # the replayed move had a second chance
        _save(g, moves, takebacks=g["takebacks"] + 1, summary=json.dumps(s))
        g = get(game_id)
        b = board_of(g)
        if b.turn != player_color(g):         # player is Black and undid their first move
            g = _engine_reply(g)
        return g


def resign(game_id: int):
    g = get(game_id)
    if not g or g["status"] != "playing":
        raise ValueError("This game is over.")
    me = player_color(g)
    return finish(g, 0.0, "1-0" if me == chess.BLACK else "0-1", "resigned")


def _check_end(g, b: chess.Board):
    o = b.outcome(claim_draw=True)
    if o is None and len(b.move_stack) < MAX_PLIES:
        return g
    me = player_color(g)
    if o is None:
        return finish(g, 0.5, "1/2-1/2", "move limit")
    if o.winner is None:
        score, res = 0.5, "1/2-1/2"
    else:
        score = 1.0 if o.winner == me else 0.0
        res = "1-0" if o.winner == chess.WHITE else "0-1"
    term = o.termination.name.lower().replace("_", " ")
    return finish(g, score, res, term)


def finish(g, score: float, result: str, termination: str):
    p = profile(g["profile_id"])
    moves = json.loads(g["moves"])
    mine = [m for m in moves if m["by"] == "p"]
    unassisted = [m for m in mine if not m.get("a")]
    share = len(unassisted) / len(mine) if mine else 0.0
    graded = [m["acc"] for m in unassisted if "acc" in m]
    mean_acc = sum(graded) / len(graded) if graded else None
    bl, nb = rating.balanced_loss([tuple(m["wc"]) for m in unassisted if "wc" in m])
    adapt = json.loads(p.get("adapt") or "{}")
    r_after = rating.update(p["rating"], p["games"], g["target"], score, share, bl, nb, adapt)
    review = review_of(g, moves, mean_acc, share)
    from . import coach                       # late import: coach imports this module
    try:
        review["rating"] = coach.game_rating(g, moves, review, p, score, termination)
    except Exception:                         # a report card must never stop a game from finishing
        import logging
        logging.getLogger("cc").exception("game rating failed")
    s = json.loads(g["summary"])
    s["review"] = review
    s["mean_accuracy"] = mean_acc
    s["unassisted_share"] = share
    s["balanced_loss"] = bl
    now = time.time()
    store.run("UPDATE games SET status='finished', ended=?, score=?, result=?, termination=?, rating_after=?, summary=? WHERE id=?",
              (now, score, result, termination, r_after, json.dumps(s), g["id"]))
    w = json.loads(p["weaknesses"] or "{}")
    w = {k: round(v * 0.9, 3) for k, v in w.items()}              # older habits fade
    for t in review["themes"]:
        w[t] = round(w.get(t, 0) + 1, 3)
    store.run("UPDATE profiles SET rating=?, games=games+1, weaknesses=?, adapt=? WHERE id=?",
              (r_after, json.dumps(w), json.dumps(adapt), p["id"]))
    return get(g["id"])


THEME_WORDS = {
    "hung_piece": "leaving a piece where it can be taken",
    "missed_tactic": "missing a chance to capture or give a strong check",
    "missed_mate": "missing a checkmate",
    "missed_threat": "missing the other side's threat",
    "early_queen": "bringing the queen out very early",
    "king_safety": "leaving the king in the middle",
}


def review_of(g, moves, mean_acc, share):
    me = player_color(g)
    themes = []
    for m in moves:
        if m["by"] == "p" and not m.get("a") and m.get("theme") and m["theme"] != "other":
            themes.append(m["theme"])
    b = chess.Board()
    castled, queen_early = False, False
    pmoves = 0
    for m in moves:
        mv = chess.Move.from_uci(m["u"])
        if m["by"] == "p":
            pmoves += 1
            pc = b.piece_at(mv.from_square)
            if pc and pc.piece_type == chess.QUEEN and pmoves <= 5:
                queen_early = True
            if b.is_castling(mv):
                castled = True
        b.push(mv)
    if queen_early:
        themes.append("early_queen")
    if pmoves >= 20 and not castled:
        ksq = b.king(me)
        if ksq is not None and chess.square_file(ksq) in (3, 4):
            themes.append("king_safety")
    counts = {}
    for t in themes:
        counts[t] = counts.get(t, 0) + 1
    cls = {}
    for m in moves:
        if m["by"] == "p" and "cls" in m:
            cls[m["cls"]] = cls.get(m["cls"], 0) + 1
    graded = [m for m in moves if m["by"] == "p" and not m.get("a") and "cls" in m]
    good = sum(1 for m in graded if m["cls"] in ("best", "good"))
    return {"themes": sorted(set(themes)), "theme_counts": counts, "classes": cls,
            "good_share": round(good / len(graded), 3) if graded else None,
            "accuracy": round(mean_acc, 1) if mean_acc is not None else None,
            "unassisted_share": round(share, 2)}


def pgn(g) -> str:
    p = profile(g["profile_id"]) or {"name": "Player"}
    game = chess.pgn.Game()
    me, bot = p["name"], f"Chess Companion (level {round(g['level'])})"
    game.headers["Event"] = "Chess Companion"
    game.headers["Site"] = "Chess Companion"
    game.headers["Date"] = time.strftime("%Y.%m.%d", time.localtime(g["started"]))
    game.headers["White"] = me if g["color"] == "white" else bot
    game.headers["Black"] = bot if g["color"] == "white" else me
    game.headers["Result"] = g["result"] if g["status"] == "finished" else "*"
    if g["termination"]:
        game.headers["Termination"] = g["termination"]
    node = game
    for m in json.loads(g["moves"]):
        node = node.add_variation(chess.Move.from_uci(m["u"]))
        if m.get("a"):
            node.comment = "assisted"
    return str(game) + "\n"
