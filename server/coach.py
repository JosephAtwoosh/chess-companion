"""The coach. Six questions, always answered from a full-strength Stockfish analysis.

1. analyse(): engine facts about the position (evaluation, best line, threats, loose pieces).
2. template answer: a short spoken answer built straight from the facts. Always available.
3. If this player may use an AI provider, the model gets ONLY the facts that answer allows
   (a hint never contains the best move) and rephrases them for this player's age and level.
   The answer is rejected (template used instead) if it names a square not in the facts.
The board drawings (arrows, circles) always come from the engine facts, never from the model.
"""
import json
import logging
import re
import time

import chess
import chess.engine

from . import config, games, llm, rating, store
from .engine import MATE, cp_of, pool, win_chance

log = logging.getLogger("cc.coach")
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop", chess.ROOK: "rook",
         chess.QUEEN: "queen", chess.KING: "king"}
KINDS = ("hint", "danger", "winning", "plan", "explain", "best")


# ------------------------------------------------------------------ words
def say_move(board: chess.Board, mv: chess.Move) -> str:
    """'knight to f3', 'bishop takes the knight on e5, check', 'castles on the king side'."""
    pc = board.piece_at(mv.from_square)
    if board.is_castling(mv):
        s = "castle on the king's side" if chess.square_file(mv.to_square) == 6 else "castle on the queen's side"
    else:
        name = NAMES[pc.piece_type]
        to = chess.square_name(mv.to_square)
        if board.is_capture(mv):
            victim = board.piece_at(mv.to_square)
            vname = NAMES[victim.piece_type] if victim else "pawn"   # en passant
            s = f"{name} takes the {vname} on {to}"
        else:
            s = f"{name} to {to}"
        if mv.promotion:
            s += f" and becomes a {NAMES[mv.promotion]}"
    after = board.copy()
    after.push(mv)
    if after.is_checkmate():
        s += ", checkmate"
    elif after.is_check():
        s += ", check"
    return s


def eval_words(wc: float, child: bool) -> str:
    if wc >= 90: return "You are winning!" if child else "You are winning clearly."
    if wc >= 70: return "You are doing better." if child else "You have a clear advantage."
    if wc >= 58: return "You are a little bit better." if child else "You are slightly better."
    if wc > 42: return "It is about equal." if child else "The position is roughly equal."
    if wc > 30: return "Your opponent is a little bit better." if child else "Your opponent is slightly better."
    if wc > 10: return "Your opponent is doing better. Keep fighting!" if child else "Your opponent has a clear advantage."
    return "Your opponent is winning, but don't give up!" if child else "You are in serious trouble."


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.color == color)


def loose_pieces(board: chess.Board, color: chess.Color):
    """Pieces of `color` that are attacked and either undefended or attacked by something cheaper."""
    out = []
    for sq, p in board.piece_map().items():
        if p.color != color or p.piece_type == chess.KING:
            continue
        attackers = board.attackers(not color, sq)
        if not attackers:
            continue
        defended = bool(board.attackers(color, sq))
        cheapest = min(VALUES[board.piece_at(a).piece_type] or 99 for a in attackers)
        if not defended or cheapest < VALUES[p.piece_type]:
            out.append((sq, p))
    out.sort(key=lambda x: -VALUES[x[1].piece_type])
    return out


# ------------------------------------------------------------------ facts
def analyse(board: chess.Board, me: chess.Color, t: float):
    infos = pool().analyse(board, chess.engine.Limit(time=t), multipv=3)
    best = infos[0]
    cp = cp_of(best["score"], me)
    pv = best["pv"][:6]
    pv_words, b = [], board.copy()
    for mv in pv:
        pv_words.append(("you: " if b.turn == me else "they: ") + say_move(b, mv))
        b.push(mv)
    f = {"cp": cp, "wc": round(win_chance(cp)), "best": pv[0] if pv else None,
         "mate_in": best["score"].pov(me).mate(), "pv_words": pv_words,
         "alternatives": [say_move(board, i["pv"][0]) for i in infos[1:] if i.get("pv")],
         "material_me": material(board, me), "material_them": material(board, not me)}
    # threats: what would the opponent do if it were their move again?
    f["threat"] = None
    if not board.is_check() and board.turn == me:
        nb = board.copy()
        nb.push(chess.Move.null())
        if not nb.is_game_over():
            ti = pool().analyse(nb, chess.engine.Limit(time=t / 2))[0]
            tcp = cp_of(ti["score"], me)
            tm = ti["pv"][0] if ti.get("pv") else None
            if tm and (cp - tcp >= 150 or (ti["score"].pov(me).mate() or 0) < 0):
                f["threat"] = {"move": tm, "words": say_move(nb, tm), "mate": (ti["score"].pov(me).mate() or 0) < 0,
                               "target": chess.square_name(tm.to_square)}
    f["my_loose"] = [(chess.square_name(s), NAMES[p.piece_type]) for s, p in loose_pieces(board, me)]
    f["their_loose"] = [(chess.square_name(s), NAMES[p.piece_type]) for s, p in loose_pieces(board, not me)]
    return f


def development(board: chess.Board, me: chess.Color):
    home = [chess.B1, chess.G1, chess.C1, chess.F1] if me == chess.WHITE else [chess.B8, chess.G8, chess.C8, chess.F8]
    still = [chess.square_name(s) for s in home if (p := board.piece_at(s)) and p.color == me
             and p.piece_type in (chess.KNIGHT, chess.BISHOP)]
    ksq = board.king(me)
    castled = ksq is not None and chess.square_file(ksq) in (6, 2) and chess.square_rank(ksq) in (0, 7)
    can_castle = board.has_castling_rights(me)
    return still, castled, can_castle


# ------------------------------------------------------------------ answers
def answer(kind: str, g, prof, hint_level: int = 1):
    if kind not in KINDS:
        raise ValueError("Unknown question")
    board = games.board_of(g)
    me = games.player_color(g)
    child = prof["age_group"] == "child"
    cfg = config.load()
    if g["status"] != "playing":
        return {"text": "This game is finished. Start a new game and I'll help you again.", "shapes": []}
    if board.turn != me and kind != "explain":
        return {"text": "Wait for the computer to move first.", "shapes": []}
    f = analyse(board, me, cfg["coach_time"]) if board.turn == me else None
    moves = json.loads(g["moves"])
    shapes, text, allowed = [], "", {}

    if kind == "winning":
        text = eval_words(f["wc"], child)
        diff = f["material_me"] - f["material_them"]
        if f["mate_in"] and f["mate_in"] > 0:
            text += " There is a checkmate for you somewhere!" if child else f" There is a forced mate in {f['mate_in']}."
        elif f["mate_in"] and f["mate_in"] < 0:
            text += " Be careful, your king is in big danger."
        elif diff:
            pts = abs(diff)
            text += f" You have {pts} point{'s' if pts > 1 else ''} {'more' if diff > 0 else 'less'} in pieces."
        allowed = {"evaluation": eval_words(f["wc"], child), "win_chance_percent": f["wc"],
                   "material_you": f["material_me"], "material_them": f["material_them"], "mate_in": f["mate_in"]}

    elif kind == "danger":
        th, loose = f["threat"], f["my_loose"]
        if board.is_check():
            text = "Yes! Your king is in check. You must get out of check."
            shapes.append({"orig": chess.square_name(board.king(me)), "brush": "red"})
            allowed = {"in_check": True}
        elif th:
            tb = board.copy(); tb.push(chess.Move.null())
            text = ("Yes. Watch out: " if child else "Yes. The threat is ") + f"they want to play {th['words']}."
            if th["mate"]:
                text = "Yes, big danger! They are threatening checkmate: " + th["words"] + "."
            shapes.append({"orig": chess.square_name(th["move"].from_square), "dest": th["target"], "brush": "red"})
            allowed = {"threat": th["words"], "threat_is_mate": th["mate"], "your_loose_pieces": loose}
        elif loose:
            sq, name = loose[0]
            text = f"Your {name} on {sq} could be taken. Can you protect it or move it?"
            shapes.append({"orig": sq, "brush": "red"})
            allowed = {"your_loose_pieces": loose}
        else:
            text = "No, you are safe right now. Have a look at what you can attack."
            allowed = {"safe": True}

    elif kind == "hint":
        mv = f["best"]
        pc = board.piece_at(mv.from_square)
        name = NAMES[pc.piece_type]
        theme = ("checkmate" if f["mate_in"] and f["mate_in"] > 0 else
                 "capture" if board.is_capture(mv) else "check" if board.gives_check(mv) else
                 "save a piece" if f["threat"] or any(s == chess.square_name(mv.from_square) for s, _ in f["my_loose"])
                 else "castle" if board.is_castling(mv) else "quiet")
        clue = {"checkmate": "There is a way to checkmate! Look at every check you can give.",
                "capture": "Look for something you can capture.",
                "check": "Can you find a strong check?",
                "save a piece": "Something of yours is under attack. What can you do about it?",
                "castle": "Think about keeping your king safe.",
                "quiet": "Think about which piece is doing the least, and find it a better square."}[theme]
        if hint_level <= 1:
            text = clue
            allowed = {"clue": clue, "theme": theme}
        elif hint_level == 2:
            text = f"{clue} The piece to move is your {name}."
            allowed = {"clue": clue, "theme": theme, "piece": name}
        else:
            sq = chess.square_name(mv.from_square)
            text = f"Try your {name} on {sq}. Where can it go? If you're still stuck, press Best move."
            shapes.append({"orig": sq, "brush": "green"})
            allowed = {"clue": clue, "theme": theme, "piece": name, "square": sq}

    elif kind == "plan":
        still, castled, can_castle = development(board, me)
        parts = []
        plies = len(board.move_stack)
        if f["threat"]:
            parts.append("First, deal with your opponent's threat.")
        if still and plies < 30:
            parts.append("Get your knights and bishops out." if child else
                         f"Develop your remaining minor pieces ({', '.join(still)}).")
        if not castled and can_castle and plies < 40:
            parts.append("Castle to keep your king safe.")
        if f["their_loose"]:
            sq, name = f["their_loose"][0]
            parts.append(f"Their {name} on {sq} looks weak. Can you win it?")
        if not parts:
            mv = f["best"]
            pc = board.piece_at(mv.from_square)
            parts.append(f"The engine likes moving your {NAMES[pc.piece_type]}. Find it a more active square, "
                         "and look for weak pieces or pawns to attack.")
        text = " ".join(parts[:3 if not child else 2])
        allowed = {"plan_points": parts, "engine_line_idea": f["pv_words"][:4], "evaluation": eval_words(f["wc"], child)}
        for sq in still[:2] if plies < 30 else []:
            shapes.append({"orig": sq, "brush": "blue"})

    elif kind == "explain":
        opp = next((m for m in reversed(moves) if m["by"] == "o"), None)
        if not opp:
            text = "The computer hasn't moved yet. You start!"
            allowed = {}
        else:
            idx = moves.index(opp)
            b0 = games.board_of({"moves": json.dumps(moves[:idx])})
            mv = chess.Move.from_uci(opp["u"])
            words = say_move(b0, mv)
            b1 = b0.copy(); b1.push(mv)
            attacked = [(chess.square_name(s), NAMES[b1.piece_at(s).piece_type]) for s in b1.attacks(mv.to_square)
                        if b1.piece_at(s) and b1.piece_at(s).color == me
                        and b1.piece_at(s).piece_type not in (chess.PAWN, chess.KING)]   # check is already said
            cp_before, _ = games._eval_before(g, moves, idx)
            sign = 1 if me == chess.WHITE else -1
            swing = win_chance(sign * opp["cp"]) - win_chance(sign * cp_before)
            text = f"The computer played {words}."
            if attacked:
                text += f" It attacks your {attacked[0][1]} on {attacked[0][0]}."
            if swing >= 15:
                text += " That was a mistake by the computer. See if you can punish it!"
            elif swing <= -10:
                text += " That was a strong move."
            shapes.append({"orig": chess.square_name(mv.from_square), "dest": chess.square_name(mv.to_square), "brush": "blue"})
            allowed = {"computer_move": words, "attacks": attacked,
                       "verdict": "mistake by computer" if swing >= 15 else "strong" if swing <= -10 else "normal",
                       "threat_now": f["threat"]["words"] if f and f["threat"] else None}

    elif kind == "best":
        mv = f["best"]
        text = f"The best move is {say_move(board, mv)}."
        if len(f["pv_words"]) > 2:
            text += f" Then if {f['pv_words'][1].split(': ', 1)[1]}, you can play {f['pv_words'][2].split(': ', 1)[1]}."
        shapes.append({"orig": chess.square_name(mv.from_square), "dest": chess.square_name(mv.to_square), "brush": "green"})
        allowed = {"best_move": say_move(board, mv), "line": f["pv_words"][:4], "evaluation": eval_words(f["wc"], child)}

    if kind in games.ASSISTING:
        hint = {"best": f["best"].uci(), "wc": win_chance(f["cp"])} if kind == "hint" and f and f["best"] else None
        games.mark_assist(g["id"], hint)
    result = {"text": text, "shapes": shapes, "source": "engine"}
    ai = _rephrase(kind, text, allowed, prof, g, cfg)
    if ai:
        result.update(text=ai[0], source=ai[1])
    return result


# ------------------------------------------------------------------ AI rephrasing
def ai_profiles(cfg):
    """Which players may use AI wording: "all" or a list of ids (set in Family settings)."""
    return cfg.get("ai_profiles", cfg["ai_subscription_profiles"])


def providers_for(prof, cfg):
    allowed = ai_profiles(cfg)
    if allowed != "all" and prof["id"] not in allowed:
        return []
    return [p for p in cfg["ai_providers"] if p in llm.PROVIDERS]


def _within_limits(prof, cfg):
    hour = store.one("SELECT COUNT(*) n FROM coach_log WHERE profile_id=? AND provider!='engine' AND ts>?",
                     (prof["id"], time.time() - 3600))["n"]
    day = store.one("SELECT COUNT(*) n FROM coach_log WHERE provider!='engine' AND ts>?", (time.time() - 86400,))["n"]
    return hour < cfg["ai_per_profile_per_hour"] and day < cfg["ai_per_day"]


SQUARE = re.compile(r"\b([a-h][1-8])\b")
_down: dict[str, float] = {}   # provider -> time until which it is skipped


def _rephrase(kind, text, allowed, prof, g, cfg):
    provs = [p for p in providers_for(prof, cfg) if _down.get(p, 0) < time.time()]
    if not provs or not _within_limits(prof, cfg):
        _log(prof, g, kind, "engine", 0, True)
        return None
    child = prof["age_group"] == "child"
    level = round(rating.opponent_level(prof["rating"], 0.5))
    weak = json.loads(prof["weaknesses"] or "{}")
    top = [games.THEME_WORDS[k] for k, v in sorted(weak.items(), key=lambda x: -x[1]) if v >= 1.5 and k in games.THEME_WORDS][:2]
    system = (
        "You are a warm, encouraging chess coach. Your words are read aloud by text-to-speech. "
        f"The player is {'a child about 8 years old' if child else 'a teenager' if prof['age_group'] == 'teen' else 'an adult'}"
        f" playing at roughly {level} rating. "
        + ("Use very simple words and at most 2 short sentences. " if child else "Use at most 3 sentences. ")
        + "Use ONLY the engine facts given. Never mention a move, piece or square that is not in the facts. "
        "Never calculate chess lines yourself. Say squares like 'e4'. No notation like Nf3, no lists, no markdown. "
        "Reply with the spoken words only."
    )
    if kind == "hint":
        system += " This is a hint: help them think, do not tell them the move."
    prompt = json.dumps({"question": {"hint": "Give me a hint", "danger": "Am I in danger?",
                                      "winning": "Am I winning?", "plan": "What is my plan?",
                                      "explain": "Explain the computer's last move", "best": "Show me the best move"}[kind],
                         "engine_facts": allowed, "draft_answer": text,
                         "player_habits_to_work_on": top}, default=str)
    ok_squares = set(SQUARE.findall(json.dumps(allowed, default=str) + " " + text))
    for p in provs:
        t = time.time()
        try:
            out = llm.PROVIDERS[p](system, prompt, cfg)
        except llm.Unavailable as e:
            log.warning("provider %s unavailable: %s", p, e)
            _down[p] = time.time() + 300
            _log(prof, g, kind, p, int((time.time() - t) * 1000), False)
            continue
        out = re.sub(r"[*_#`]", "", out).strip()
        bad = set(SQUARE.findall(out)) - ok_squares
        if bad or len(out) > 600 or not out:
            log.info("rejected %s answer (squares %s)", p, bad)
            _log(prof, g, kind, p, int((time.time() - t) * 1000), False)
            return None
        _log(prof, g, kind, p, int((time.time() - t) * 1000), True)
        return out, p
    return None


def _log(prof, g, kind, provider, ms, ok):
    store.run("INSERT INTO coach_log(ts,profile_id,game_id,kind,provider,ms,ok) VALUES(?,?,?,?,?,?,?)",
              (time.time(), prof["id"], g["id"], kind, provider, ms, int(ok)))


# ------------------------------------------------------------------ after a hint: was the move good?
def move_feedback(g, prof):
    """Spoken verdict on the player's move that followed a hint (None if no hint was asked)."""
    s = json.loads(g["summary"])
    req = s.pop("feedback_request", None)
    if not req:
        return None
    store.run("UPDATE games SET summary=? WHERE id=?", (json.dumps(s), g["id"]))
    moves = json.loads(g["moves"])
    idx = req["ply"]
    if idx >= len(moves):
        return None
    me = games.player_color(g)
    child = prof["age_group"] == "child"
    before = games.board_of({"moves": json.dumps(moves[:idx])})
    mv = chess.Move.from_uci(moves[idx]["u"])
    best = chess.Move.from_uci(req["best"])
    after = before.copy()
    after.push(mv)
    if after.is_checkmate():
        return {"text": "Checkmate! Brilliant!" if child else "Checkmate. Well played!", "source": "engine"}
    if mv == best:
        return {"text": ("Well done, you found it! That's the move I was thinking of." if child
                         else "Well done, that's exactly the move the hint was pointing to."), "source": "engine"}
    info = pool().analyse(after, chess.engine.Limit(time=config.load()["coach_time"]))[0]
    wc_after = win_chance(cp_of(info["score"], me))
    loss = req["wc"] - wc_after
    better = say_move(before, best)
    if loss < 5:
        text = ("Good move! That works just as well as my idea." if child
                else f"Good move. That's as good as {better}, which the hint was pointing to.")
    elif loss < 12:
        text = (f"That's OK, but {better} was a bit better." if child
                else f"Reasonable, but {better} was a little stronger.")
    else:
        text = (f"Hmm, that wasn't the best. {better} was much better." if child
                else f"That was a mistake. {better} was much stronger.")
        reply = info["pv"][0] if info.get("pv") else None
        if reply is not None:
            victim = after.piece_at(reply.to_square)
            if (info["score"].pov(me).mate() or 0) < 0:
                text += " Now the computer has a way to checkmate you. Look carefully!"
            elif after.is_capture(reply) and victim and victim.color == me:
                text += f" Now your {NAMES[victim.piece_type]} on {chess.square_name(reply.to_square)} can be taken."
            elif after.gives_check(reply):
                text += " It lets the computer give a strong check."
    return {"text": text, "source": "engine"}


# ------------------------------------------------------------------ end-of-game report card
HEADLINES = {5: "Superb game!", 4: "Great game!", 3: "Good game!", 2: "Nice effort!", 1: "Good try!"}
TIPS = {
    "hung_piece": "Before each move, check: can anything of mine be taken?",
    "missed_tactic": "Before each move, look at every capture and every check you can make.",
    "missed_mate": "When the enemy king is short of squares, look for a checkmate.",
    "missed_threat": "After the computer moves, ask: what is it trying to do?",
    "early_queen": "Bring out your knights and bishops before your queen.",
    "king_safety": "Castle early to keep your king safe.",
}


def game_rating(g, moves, review, prof, score, termination):
    """Stars (1-5) and a short, encouraging spoken summary of a finished game.

    Stars come from the share of good-or-best unassisted moves (how well you played, not just the
    result), with an extra star for beating your own recent average, so improvement is rewarded
    even in a defeat."""
    child = prof["age_group"] == "child"
    me = games.player_color(g)
    mine = [(i, m) for i, m in enumerate(moves) if m["by"] == "p"]
    graded = [m for _, m in mine if not m.get("a") and "cls" in m]
    share = review.get("good_share")
    if share is None or len(graded) < 3:
        stars = 3 if score > 0 else 2
    else:
        stars = 5 if share >= 0.9 else 4 if share >= 0.78 else 3 if share >= 0.62 else 2 if share >= 0.45 else 1
    past = [json.loads(r["summary"]).get("review", {}).get("good_share") for r in store.q(
        "SELECT summary FROM games WHERE profile_id=? AND status='finished' AND id!=? ORDER BY id DESC LIMIT 10",
        (prof["id"], g["id"]))]
    past = [x for x in past if x is not None]
    personal_best = False
    if share is not None and len(past) >= 3 and share >= sum(past) / len(past) + 0.05:
        stars, personal_best = min(5, stars + 1), True
    if score == 1.0:
        stars = max(stars, 3)                 # a win is always worth at least three stars
    stars = max(1, stars)

    # best moment: the player's strongest capture or check that was the engine's top (or a good) move
    best_txt, best_val = None, 0
    b = chess.Board()
    for i, m in enumerate(moves):
        mv = chess.Move.from_uci(m["u"])
        if m["by"] == "p" and m.get("cls") in ("best", "good"):
            val = 0
            if b.is_capture(mv):
                victim = b.piece_at(mv.to_square)
                val = VALUES[victim.piece_type] if victim else 1
            nxt = b.copy(); nxt.push(mv)
            if nxt.is_checkmate():
                val = 100
            elif nxt.is_check():
                val += 0.5
            if val > best_val:
                best_val, best_txt = val, f"move {i // 2 + 1}, {say_move(b, mv)}"
        b.push(mv)
    found = sum(1 for m in graded if m["cls"] == "best")

    head = HEADLINES[stars]
    if score == 1.0:
        head = "You won! " + head
    elif score == 0.5:
        head = "A draw! " + head
    parts = [head]
    if personal_best:
        parts.append("That's better than your recent games. You're improving!")
    if found >= 2:
        parts.append(f"You found the computer's favourite move {found} times.")
    if best_txt and best_val >= 3:
        parts.append(f"Your best moment was {best_txt}.")
    elif best_txt and best_val >= 0.5 and child:
        parts.append(f"I liked {best_txt}.")
    tip_key = next((t for t, _ in sorted(review.get("theme_counts", {}).items(), key=lambda x: -x[1]) if t in TIPS), None)
    if tip_key:
        parts.append(("One thing to practise: " if child else "Tip for next time: ") + TIPS[tip_key])
    elif score < 1.0:
        parts.append("Keep playing and you'll keep getting stronger!" if child else "Keep it up!")
    return {"stars": stars, "headline": head, "text": " ".join(parts), "found_best": found,
            "best_moment": best_txt, "tip": TIPS.get(tip_key) if tip_key else None, "personal_best": personal_best}
