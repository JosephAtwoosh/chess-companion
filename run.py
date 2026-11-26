"""Start the Chess Companion backend.  python run.py [--data DIR] [--port N]
Used by the scheduled task (tools/install-service.ps1) and for local testing."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if "--data" in sys.argv:
    os.environ["CC_DATA"] = sys.argv[sys.argv.index("--data") + 1]
if "--port" in sys.argv:
    os.environ["CC_PORT"] = sys.argv[sys.argv.index("--port") + 1]

from server.app import main  # noqa: E402

main()
