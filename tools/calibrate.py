"""Measure the average move accuracy of the move picker at each level (equal-level games).

  python tools/calibrate.py <level> <games> <out.json>
Feeds rating.CALIBRATION (accuracy -> level), used to speed up the first few games.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chess

from server import rating
from server.engine import Pool
from tools.simulate import play

level, n, out = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
pool = Pool(size=1, threads=1, hash_mb=32)
rng = random.Random(int(level))
accs, scores = [], []
for i in range(n):
    s, a, how = play(pool, level, level, chess.WHITE if i % 2 == 0 else chess.BLACK, rng)
    accs += a
    scores.append(s)
    Path(out).write_text(json.dumps({"level": level, "games": i + 1, "moves": len(accs),
                                     "balanced_loss": rating.balanced_loss(accs)[0], "balanced_moves": rating.balanced_loss(accs)[1], "white_score": scores}))
pool.close()
print(Path(out).read_text())
