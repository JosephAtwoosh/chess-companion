"""Switch on the website relay.  python tools/relay_key.py https://your-site.example/chess-companion/api.php

- makes a new random key in the relay_key_file setting (default data/secrets/relay.key; never printed, never uploaded),
- writes web-live/data/relay.json containing only the key's SHA-256 (upload this file to the
  website as chess-companion/data/relay.json),
- sets relay_url in data/settings.json so the backend starts polling after its next restart.
Run again any time to replace the key (then upload the new relay.json).
"""
import hashlib
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import config  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)
url = sys.argv[1]
if not url.startswith("https://"):
    sys.exit("The relay URL must be https://")
cfg = config.load()
keyfile = Path(cfg["relay_key_file"])
keyfile.parent.mkdir(parents=True, exist_ok=True)
key = secrets.token_urlsafe(32)
keyfile.write_text(key, encoding="utf-8")
out = config.ROOT / "web-live" / "data" / "relay.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"mode": "mailbox", "worker_key_sha256": hashlib.sha256(key.encode()).hexdigest()}))
config.save({"relay_url": url})
print(f"Key saved to {keyfile}")
print(f"Upload {out} to the website as chess-companion/data/relay.json")
print("Then restart the backend:  schtasks /end /tn \"Chess Companion\" & schtasks /run /tn \"Chess Companion\"")
