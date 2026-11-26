"""Mailbox relay test: the live-site path, simulated on this PC.

Two PHP built-in servers share one copy of web/ in mailbox mode (as on the Linux hosting):
port 8781 is "the browser's" side, port 8782 is where the home server's relay worker polls. A request sent
to 8781 must be answered by the backend through the queue.   python -m unittest tests.test_relay
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="cc-relay-"))
os.environ["CC_DATA"] = str(TMP / "backend")
sys.path.insert(0, str(ROOT))

from server import app, config  # noqa: E402


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


A, B, C = free_port(), free_port(), free_port()   # browser side, worker poll, worker reply


def post(url, obj, headers=None):
    req = urllib.request.Request(url, json.dumps(obj).encode(), {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


@unittest.skipIf(shutil.which("php") is None, "php not installed")
class Relay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        site = TMP / "site"
        shutil.copytree(ROOT / "web", site)
        key = "relay-test-key-" + os.urandom(8).hex()
        (site / "data").mkdir(exist_ok=True)
        (site / "data" / "relay.json").write_text(json.dumps(
            {"mode": "mailbox", "worker_key_sha256": hashlib.sha256(key.encode()).hexdigest()}))
        keyfile = TMP / "relay.key"
        keyfile.write_text(key)
        env = dict(os.environ, CC_RELAY_MODE="mailbox", CC_RELAY_ALLOW_HTTP="1")
        cls.procs = []
        cls.procs = [subprocess.Popen(["php", "-S", f"127.0.0.1:{p}", "-t", str(site)], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for p in (A, B, C)]
        time.sleep(1.5)
        config.save({"relay_url": f"http://127.0.0.1:{B}/api.php", "relay_reply_url": f"http://127.0.0.1:{C}/api.php", "relay_key_file": str(keyfile), "ai_providers": []})
        cls.stop = threading.Event()
        threading.Thread(target=app.relay_worker, args=(cls.stop,), daemon=True).start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.stop.set()
        for p in cls.procs:
            p.terminate()
        from server.engine import pool
        pool().close()

    def test_round_trip(self):
        t = time.time()
        st = post(f"http://127.0.0.1:{A}/api.php", {"op": "status"})
        self.assertTrue(st["ok"])
        self.assertLess(time.time() - t, 5)
        r = post(f"http://127.0.0.1:{A}/api.php", {"op": "login", "password": "nope"})
        self.assertFalse(r["ok"])

    def test_worker_needs_key(self):
        req = urllib.request.Request(f"http://127.0.0.1:{C}/api.php?worker=poll", headers={"X-Relay-Key": "wrong"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 403)

    def test_data_not_served(self):
        # PHP's built-in server has no .htaccess; this only checks api.php rejects GET for browsers
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{A}/api.php", timeout=10)
        self.assertEqual(e.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
