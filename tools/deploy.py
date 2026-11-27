r"""Copy the web app (web/) to a website folder.  python tools/deploy.py [C:\website\chess-companion]
Never copies or overwrites data/ contents (the live relay queue and relay.json live there).

Every upload is stamped: '?v=dev' in the HTML pages and VERSION = 'dev' in sw.js become a short hash of
the files, so phones fetch the new app.js / style.css instead of reusing old copies."""
import hashlib
import shutil
import sys
from pathlib import Path

src = Path(__file__).resolve().parent.parent / "web"
dst = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\website\chess-companion")
STAMPED = {"index.html", "admin.html", "sw.js"}

files = sorted(f for f in src.rglob("*") if f.is_file()
               and not (f.relative_to(src).parts[0] == "data" and f.name != ".htaccess"))
h = hashlib.sha256()
for f in files:
    h.update(f.relative_to(src).as_posix().encode() + b"\0" + f.read_bytes())
version = h.hexdigest()[:10]

dst.mkdir(parents=True, exist_ok=True)
for f in files:
    rel = f.relative_to(src)
    out = dst / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    if rel.as_posix() in STAMPED:
        text = f.read_text(encoding="utf-8")
        text = text.replace("?v=dev", f"?v={version}").replace("const VERSION = 'dev';", f"const VERSION = '{version}';")
        out.write_text(text, encoding="utf-8", newline="")
    else:
        shutil.copy2(f, out)
print(f"copied {len(files)} files to {dst} (version {version})")
