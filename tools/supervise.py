"""Keep the backend running: restart it if it stops (crash, Stockfish trouble, etc.).
Started at boot by the scheduled task "Chess Companion" (tools/install-service.ps1).
Logs: data/logs/supervisor.log (and the backend's own data/logs/server.log)."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "logs" / "supervisor.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
py = Path(sys.executable).with_name("python.exe")   # console-less parent (pythonw) runs a normal child
backoff = 2
while True:
    start = time.time()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " starting backend\n")
    p = subprocess.run([str(py), str(ROOT / "run.py")], cwd=ROOT, creationflags=0x08000000,
                       stdout=subprocess.DEVNULL, stderr=open(LOG, "a", encoding="utf-8"))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" backend exited with {p.returncode}\n")
    backoff = 2 if time.time() - start > 300 else min(60, backoff * 2)
    time.sleep(backoff)
