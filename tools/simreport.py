"""Summarise sim/run-*.json (written by tools/simulate.py)."""
import glob, json, sys
rows = []
for f in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else "sim/run-*.json")):
    d = json.load(open(f)); g = d["games"]; s = [x["score"] for x in g]; lv = [x["opp_level"] for x in g]
    late = s[15:]
    first10 = next((i + 1 for i in range(len(lv)) if abs(lv[i] - lv[-1]) < 120 and all(abs(v - lv[-1]) < 200 for v in lv[i:])), None)
    rows.append(f"| {d['player']:.0f} | {d['target']:.0%} | {len(g)} | {sum(s)/len(s):.0%} | "
                f"{(sum(late)/len(late) if late else float('nan')):.0%} (n={len(late)}) | {lv[0]} -> {lv[-1]} | {first10} |")
print("| simulated player level | target | games | win rate, all | win rate, games 16+ | opponent level first -> last | settled by game |")
print("|---|---|---|---|---|---|---|")
print("\n".join(rows))
