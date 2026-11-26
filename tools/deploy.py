r"""Copy the web app (web/) to a website folder.  python tools/deploy.py [C:\website\chess-companion]
Never copies or overwrites data/ contents (the live relay queue and relay.json live there)."""
import shutil, sys
from pathlib import Path
src = Path(__file__).resolve().parent.parent / "web"
dst = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\website\chess-companion")
dst.mkdir(parents=True, exist_ok=True)
n = 0
for f in src.rglob("*"):
    rel = f.relative_to(src)
    if f.is_dir() or (rel.parts[0] == "data" and rel.name != ".htaccess"):
        continue
    out = dst / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, out); n += 1
print(f"copied {n} files to {dst}")
