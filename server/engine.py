"""A small pool of Stockfish processes shared by the opponent, the move grader and the coach.

Each caller borrows one engine for one analysis. A crashed engine is restarted on next use.
"""
import logging
import math
import queue
import threading

import chess
import chess.engine

from . import config

MATE = 100000
logging.getLogger("chess.engine").setLevel(logging.ERROR)


def cp_of(score: chess.engine.PovScore, color: chess.Color) -> int:
    """Centipawns from `color`'s point of view, mates mapped to +-(MATE - distance)."""
    return score.pov(color).score(mate_score=MATE)


def win_chance(cp: int) -> float:
    """Lichess-style win percentage 0..100 from centipawns."""
    cp = max(-1500, min(1500, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def accuracy(wc_before: float, wc_after: float) -> float:
    """Lichess move accuracy (0..100) from the mover's win chance before/after the move."""
    loss = max(0.0, wc_before - wc_after)
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669))


class Pool:
    def __init__(self, size=None, threads=None, hash_mb=None, path=None):
        cfg = config.load()
        self.size = size or cfg["engines"]
        self.threads = threads or cfg["engine_threads"]
        self.hash_mb = hash_mb or cfg["engine_hash_mb"]
        self.path = str(path or config.STOCKFISH)
        self._q: "queue.Queue[chess.engine.SimpleEngine | None]" = queue.Queue()
        for _ in range(self.size):
            self._q.put(None)  # started lazily
        self._lock = threading.Lock()

    def _start(self):
        eng = chess.engine.SimpleEngine.popen_uci(self.path)
        eng.configure({"Threads": self.threads, "Hash": self.hash_mb})
        return eng

    def analyse(self, board: chess.Board, limit: chess.engine.Limit, multipv: int = 1):
        """Returns a list of info dicts (one per PV), best first."""
        eng = self._q.get()
        try:
            if eng is None:
                eng = self._start()
            try:
                infos = eng.analyse(board, limit, multipv=multipv, game=object())  # fresh search each time
            except (chess.engine.EngineTerminatedError, chess.engine.EngineError):
                try:
                    eng.quit()
                except Exception:
                    pass
                eng = self._start()
                infos = eng.analyse(board, limit, multipv=multipv, game=object())
            if isinstance(infos, dict):
                infos = [infos]
            return [i for i in infos if "pv" in i and i["pv"]] or infos
        finally:
            self._q.put(eng)

    def close(self):
        while not self._q.empty():
            eng = self._q.get_nowait()
            if eng is not None:
                try:
                    eng.quit()
                except Exception:
                    pass


_pool = None
_pool_lock = threading.Lock()


def pool() -> Pool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = Pool()
        return _pool
