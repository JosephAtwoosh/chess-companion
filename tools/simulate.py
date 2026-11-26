"""Does the adaptive opponent converge to the chosen win rate?  Real Stockfish games.

A "simulated player" is the same human-like move picker at a FIXED level (its true strength,
unknown to the system). It plays a series of games against the adaptive opponent, which starts
from the default estimate and adapts after every game exactly as the app does (rating.py).

  python tools/simulate.py --player 1000 --target 0.65 --games 60 --seed 1 --out sim.json
  python tools/simulate.py --model     # fast check of the controller alone (logistic model)

Speed-ups that do not change the controller: games are adjudicated when one side is ahead by
8+ points of material for 10 plies after move 30 (weak simulated players otherwise shuffle a
queen up for 300 moves), and capped at 240 plies (draw).
Each player move is graded with a short full-strength search so we also get the
accuracy -> level calibration used for the first games (rating.CALIBRATION).
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess
import chess.engine

from server import opponent, rating
from server.coach import material
from server.engine import Pool, accuracy, cp_of, win_chance


def play(pool, player_level, opp_level, player_color, rng, grade=True):
    b = chess.Board()
    lead_plies = 0
    accs = []
    prev_wc = None
    while not b.is_game_over(claim_draw=True) and len(b.move_stack) < 240:
        lvl = player_level if b.turn == player_color else opp_level
        if b.turn == player_color and grade:
            i0 = pool.analyse(b, chess.engine.Limit(depth=10))[0]
            wc0 = win_chance(cp_of(i0["score"], player_color))
        mv, _ = opponent.choose(b, lvl, rng, eng=pool)
        mover = b.turn
        b.push(mv)
        if mover == player_color and grade:
            if b.is_checkmate():
                wc1 = 100.0
            elif b.is_game_over():
                wc1 = 50.0
            else:
                i1 = pool.analyse(b, chess.engine.Limit(depth=10))[0]
                wc1 = win_chance(cp_of(i1["score"], player_color))
            accs.append((wc0, wc1))
        diff = material(b, player_color) - material(b, not player_color)
        if len(b.move_stack) > 60 and abs(diff) >= 8:
            lead_plies += 1
            if lead_plies >= 10:
                return (1.0 if diff > 0 else 0.0), accs, "adjudicated"
        else:
            lead_plies = 0
    o = b.outcome(claim_draw=True)
    if o is None or o.winner is None:
        return 0.5, accs, "draw"
    return (1.0 if o.winner == player_color else 0.0), accs, o.termination.name.lower()


def series(player_level, target, n_games, seed, age="adult", out=None):
    rng = random.Random(seed)
    pool = Pool(size=1, threads=1, hash_mb=32)
    r, games, adapt = rating.START[age], 0, {}
    log = []
    for gi in range(n_games):
        L = rating.opponent_level(r, target)
        color = chess.WHITE if gi % 2 == 0 else chess.BLACK
        t = time.time()
        score, accs, how = play(pool, player_level, L, color, rng)
        bl, nb = rating.balanced_loss(accs)
        r = rating.update(r, games, target, score, 1.0, bl, nb, adapt)
        mean_acc = bl
        games += 1
        log.append({"game": gi + 1, "opp_level": round(L), "score": score, "how": how,
                    "bloss": round(mean_acc, 2) if mean_acc is not None else None, "secs": round(time.time() - t, 1)})
        print(json.dumps(log[-1]), flush=True)
        if out:
            Path(out).write_text(json.dumps({"player": player_level, "target": target, "games": log}, indent=1))
    pool.close()
    return log


def model(runs=2000, games=60):
    """Controller alone, with a logistic player whose true level the controller doesn't know."""
    rng = random.Random(0)
    print("true_level target  winrate(games 21-60)  final_opp_level  max_single_change(after game 20)")
    for true, target in [(300, 0.65), (900, 0.65), (1500, 0.7), (2100, 0.5), (900, 0.3), (900, 0.9)]:
        wins, levels, jumps = 0.0, [], []
        for _ in range(runs // 6):
            r, n, adapt = rating.START["adult"], 0, {}
            for g in range(games):
                L = rating.opponent_level(r, target)
                p = 1 / (1 + 10 ** ((L - true) / 400))
                s = 1.0 if rng.random() < p else 0.0
                r2 = rating.update(r, n, target, s, 1.0, None, 0, adapt)
                if g >= 20:
                    wins += s
                    jumps.append(abs(r2 - r))
                r, n = r2, n + 1
            levels.append(rating.opponent_level(r, target))
        print(f"{true:10} {target:6} {wins / ((runs // 6) * (games - 20)):10.3f}"
              f" {sum(levels) / len(levels):16.0f} {max(jumps):12.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", type=float, default=1000)
    ap.add_argument("--target", type=float, default=0.65)
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--age", default="adult")
    ap.add_argument("--out")
    ap.add_argument("--model", action="store_true")
    a = ap.parse_args()
    if a.model:
        model()
    else:
        series(a.player, a.target, a.games, a.seed, a.age, a.out)
