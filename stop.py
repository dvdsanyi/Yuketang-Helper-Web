import json, os, signal, subprocess, sys
from pathlib import Path

PID_FILE = Path(__file__).resolve().parent / "logs" / "pids.json"


def _kill(pid: int):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def stop():
    if not PID_FILE.exists():
        return
    for name, pid in json.loads(PID_FILE.read_text()).items():
        _kill(pid)
        print(f"Stopped {name} (PID {pid})")
    PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    stop()
