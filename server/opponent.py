"""The adaptive opponent: one strength number ("level", roughly Elo-like, 100..3000) -> a human-like move.

How a move is chosen (see DECISIONS.md):
  1. Stockfish looks at the position with a *shallow* search whose depth grows with the level, and
     returns up to 10 candidate moves with their scores (MultiPV).
  2. Each candidate's cost is the drop in win chance compared with the best candidate.
  3. A move is drawn at random with weight exp(-cost / T). T (the "temperature") is large for weak
     levels, so they often play the second- or fifth-best move, and tiny for strong levels.
  4. Weak levels also like "natural-looking" moves (captures, checks, moving an attacked piece), and
     now and then make a plain oversight among natural moves.
Because the search is shallow, the mistakes are the kind people make: moves that look fine at a
glance but drop a piece to a two-move tactic. That leaves tactics for the player to find. No game
outcome is decided in advance; every move is drawn afresh.
"""
import random

import chess
import chess.engine

from .engine import cp_of, pool, win_chance

MIN_LEVEL, MAX_LEVEL = 100, 3000


def params(level: float):
    x = max(0.0, min(1.0, (level - MIN_LEVEL) / (2800 - MIN_LEVEL)))
    depth = max(1, round(1 + 15 * x ** 1.3))
    temperature = 0.4 + 30 * (1 - x) ** 2.5        # in win-chance points
    natural_bonus = 1.2 * (1 - x) ** 2              # preference for captures/checks
    oversight = 0.10 * (1 - x) ** 3                 # chance of a careless natural move
    return depth, temperature, natural_bonus, oversight


def naturalness(board: chess.Board, move: chess.Move) -> float:
    n = 0.0
    if board.is_capture(move):
        n += 1.0
    if board.gives_check(move):
        n += 0.7
    if board.is_attacked_by(not board.turn, move.from_square):
        n += 0.5                                     # "my piece is attacked, move it"
    if move.promotion:
        n += 1.0
    return n


def choose(board: chess.Board, level: float, rng: random.Random | None = None, eng=None):
    """Pick a move for the side to move. Returns (move, info) where info explains the choice."""
    rng = rng or random
    legal = list(board.legal_moves)
    if len(legal) == 1:
        return legal[0], {"reason": "only move"}
    depth, temp, nat_bonus, oversight = params(level)
    eng = eng or pool()
    infos = eng.analyse(board, chess.engine.Limit(depth=depth), multipv=min(10, len(legal)))
    cands = []
    for i in infos:
        mv = i["pv"][0]
        cands.append((mv, win_chance(cp_of(i["score"], board.turn))))
    best_wc = max(wc for _, wc in cands)

    if rng.random() < oversight:
        # a careless "natural" move: any legal move, weighted towards captures/checks/attacked pieces
        weights = [0.3 + naturalness(board, m) for m in legal]
        mv = rng.choices(legal, weights=weights)[0]
        return mv, {"reason": "oversight", "depth": depth}

    import math
    scores = []
    for mv, wc in cands:
        cost = best_wc - wc
        scores.append(-cost / temp + nat_bonus * naturalness(board, mv))
    top = max(scores)
    weights = [math.exp(s - top) for s in scores]
    k = rng.choices(range(len(cands)), weights=weights)[0]
    return cands[k][0], {"reason": "search", "depth": depth, "rank": k + 1,
                         "cost": round(best_wc - cands[k][1], 1)}
