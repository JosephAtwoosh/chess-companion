"""Estimating the player's strength and choosing the opponent level.

R = the player's estimated strength on the opponent's level scale.
Opponent level L = R - 400*log10(p/(1-p)) for the target win rate p, so that by the logistic
(Elo) model the player's expected score against L is exactly p.

After every finished game:  R += K * w * (score - p)
  score = 1 win, 0.5 draw, 0 loss;  w = share of the player's moves made without help
  K starts at 160 and shrinks to 32 (no big swings later) - but, following Kesten's rule, it only
  shrinks when the results flip between "better than target" and "worse than target". A run of
  one-sided results (opponent clearly too strong or too weak) keeps the steps big, so a first
  guess that is far off is corrected in a handful of games instead of fifty.
This is a Robbins-Monro search for the level where the player scores p, so it converges to the
target win rate even if our level scale is not a true Elo scale.

For the first games we also use move quality: the "balanced loss" of the player's unassisted
moves (average drop in win chance, counted only in positions that were still balanced, 25-75%)
maps to a level (calibrated by tools/calibrate.py, see CALIBRATION below), blended in with a
weight that fades out after 6 games. Plain accuracy does not work for this: once a game is lost
every move looks "accurate".
"""
import math

START = {"child": 500.0, "teen": 900.0, "adult": 1100.0}

# balanced loss (win-chance points per move) -> level, falling. From tools/calibrate.py (DECISIONS.md).
CALIBRATION = [(9.5, 100.0), (8.5, 300.0), (7.2, 800.0), (5.5, 1200.0),
               (3.8, 1500.0), (1.8, 2000.0), (0.8, 2600.0)]


def balanced_loss(pairs):
    """pairs = [(win chance before, after)] for the player's moves -> (mean loss, count)."""
    ls = [max(0.0, a - b) for a, b in pairs if 25 <= a <= 75]
    return (sum(ls) / len(ls) if ls else None), len(ls)


def offset(target: float) -> float:
    p = min(0.9, max(0.1, target))
    return 400 * math.log10(p / (1 - p))


def opponent_level(r: float, target: float) -> float:
    return max(100.0, min(3000.0, r - offset(target)))


def k_factor(changes: int) -> float:
    return max(32.0, 160.0 / math.sqrt(1 + changes))


def level_from_loss(loss: float) -> float:
    pts = CALIBRATION
    if loss >= pts[0][0]:
        return pts[0][1]
    for (a0, l0), (a1, l1) in zip(pts, pts[1:]):
        if loss >= a1:
            return l0 + (l1 - l0) * (a0 - loss) / (a0 - a1)
    return pts[-1][1]


def update(r: float, games: int, target: float, score: float, unassisted_share: float,
           balanced: float | None, moves_graded: int, adapt: dict | None = None) -> float:
    """New strength estimate. `adapt` ({"changes", "last"}) is updated in place (Kesten's rule)."""
    adapt = adapt if adapt is not None else {}
    err = score - target
    sign = (err > 0) - (err < 0)
    if sign and adapt.get("last") and sign != adapt["last"]:
        adapt["changes"] = adapt.get("changes", 0) + 1
    if sign:
        adapt["last"] = sign
    k = k_factor(adapt.get("changes", 0))
    r2 = r + k * max(0.0, min(1.0, unassisted_share)) * err
    if balanced is not None and moves_graded >= 8 and games < 6:
        w = 0.35 * (1 - games / 6)
        r2 = (1 - w) * r2 + w * level_from_loss(balanced)
    return max(-500.0, min(3400.0, r2))
