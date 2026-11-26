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


def kill_on_close_job():
    """A Windows job object that ends the backend (and its Stockfish engines) when this supervisor ends,
    e.g. on `schtasks /end`. Without it the backend would keep running the old code after a restart."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.LimitFlags = 0x2000                     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return lambda proc: k32.AssignProcessToJobObject(job, int(proc._handle))


assign = kill_on_close_job()
backoff = 2
while True:
    start = time.time()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " starting backend\n")
    with open(LOG, "a", encoding="utf-8") as err:
        p = subprocess.Popen([str(py), str(ROOT / "run.py")], cwd=ROOT, creationflags=0x08000000,
                             stdout=subprocess.DEVNULL, stderr=err)
        if assign and not assign(p):
            err.write("warning: could not tie the backend to the supervisor\n")
            err.flush()
        p.wait()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" backend exited with {p.returncode}\n")
    backoff = 2 if time.time() - start > 300 else min(60, backoff * 2)
    time.sleep(backoff)
